import jax
import jax.numpy as jnp


from pmwd.particles import Particles, ptcl_rpos
from pmwd.pm_util import rfftnfreq
from pmwd.spec_util import powspec
from pmwd.sto.util import scatter_dens, pv2ptcl


def _loss_mse(f, g, log=True, norm=True, weights=None):
    """MSE between two arrays, with optional modifications."""
    loss = jnp.abs(f - g)**2

    if weights is not None:
        loss *= weights

    loss = jnp.sum(loss)

    if norm:
        loss /= jnp.sum(jnp.abs(g)**2)
    else:
        loss /= len(f)  # simple mean

    if log:
        loss = jnp.log(loss)

    return loss


def _loss_power_w(f, g, spacing=1, log=True, w=None, cut_nyq=True):
    # f (model) & g (target) are fields of the same shape in configuration space
    k, P_d, N, bins = powspec(f - g, spacing, w=w, cut_nyq=cut_nyq)
    k, P_g, N, bins = powspec(g, spacing, cut_nyq=cut_nyq)
    loss = (P_d / P_g).sum() / len(k)
    if log:
        loss = jnp.log(loss)
    return loss


def _loss_power_ln(f, g, spacing=1, cut_nyq=True):
    k, P_d, N, bins = powspec(f - g, spacing, cut_nyq=cut_nyq)
    k, P_g, N, bins = powspec(g, spacing, cut_nyq=cut_nyq)
    loss = jnp.log(P_d / P_g).sum() / len(k)
    return loss


def loss_func(ptcl, tgt, conf, loss_mesh_shape=1):

    # get the target ptcl
    ptcl_t = pv2ptcl(*tgt, ptcl.pmid, ptcl.conf)

    # get the disp from particles' grid Lagrangian positions
    # may be necessary since we have it divided in the mse
    disp, disp_t = (ptcl_rpos(p, Particles.gen_grid(p.conf), p.conf)
                    for p in (ptcl, ptcl_t))
    # disp = ptcl.disp
    # reshape -> make last 3 axes spatial dims
    shape_ = (-1,) + conf.ptcl_grid_shape
    disp = disp.T.reshape(shape_)
    disp_t = disp_t.T.reshape(shape_)
    # disp_k = jnp.fft.rfftn(disp, axes=range(-3, 0))
    # disp_t_k = jnp.fft.rfftn(disp_t, axes=range(-3, 0))
    # kvec_disp = rfftnfreq(conf.ptcl_grid_shape, conf.ptcl_spacing, dtype=conf.float_dtype)

    # get the density fields
    (dens, dens_t), (loss_mesh_shape, cell_size) = scatter_dens(
                                        (ptcl, ptcl_t), conf, loss_mesh_shape)
    # dens_k = jnp.fft.rfftn(dens)
    # dens_t_k = jnp.fft.rfftn(dens_t)
    # kvec_dens = rfftnfreq(loss_mesh_shape, cell_size, dtype=conf.float_dtype)

    loss = 0.

    # displacement
    loss += _loss_mse(disp, disp_t)

    # density field
    # loss += _loss_power_w(dens, dens_t)
    loss += _loss_power_ln(dens, dens_t)

    # loss /= len(conf.a_nbody)  # divided by the number of nbody steps

    return loss