import jax
import jax.numpy as jnp


from pmwd.particles import Particles, ptcl_rpos
from pmwd.pm_util import rfftnfreq
from pmwd.spec_util import powspec
from pmwd.sto.util import ptcl2dens, power_tfcc


def _loss_mse(f, g, weights=None, log=True):
    """Simple mse between two arrays, with optional weights."""
    loss = jnp.average(jnp.abs(f - g)**2, weights=weights)
    if log: loss = jnp.log(loss)
    return loss


@jax.custom_vjp
def _loss_scale_wmse(kvec, f, g, k2pow):
    # mse of two fields in Fourier space, uniform weights
    k2 = sum(k**2 for k in kvec)
    d = f - g
    loss = jnp.sum(jnp.where(k2 != 0, jnp.abs(d)**2 / k2**k2pow, 0)
                   ) / jnp.array(d.shape).prod()
    return jnp.log(loss), (loss, k2, d, k2pow)

def _scale_wmse_fwd(kvec, f, g, k2pow):
    loss, res = _loss_scale_wmse(kvec, f, g, k2pow)
    return loss, res

def _scale_wmse_bwd(res, loss_cot):
    loss, k2, d, k2pow = res
    d_shape = d.shape
    abs_valgrad = jax.value_and_grad(jnp.abs)
    d, d_grad = jax.vmap(abs_valgrad)(d.ravel())
    d = d.reshape(d_shape)
    d_grad = d_grad.reshape(d_shape)

    loss_cot /= loss
    f_cot = loss_cot * jnp.where(k2 != 0, 2 * d * d_grad / k2**k2pow, 0
                                 ) / jnp.array(d_shape).prod()
    return None, f_cot, None, None

_loss_scale_wmse.defvjp(_scale_wmse_fwd, _scale_wmse_bwd)


def _loss_tfcc(dens, dens_t, cell_size, wtf=1):
    k, tf, cc = power_tfcc(dens, dens_t, cell_size)
    return wtf * jnp.sum((1 - tf)**2) + jnp.sum((1 - cc)**2)


def _loss_Lanzieri(disp, disp_t, dens, dens_t, cell_size):
    """The loss defined by Eq.(4) in 2207.05509v2 (Lanzieri2022)."""
    loss = jnp.sum((disp - disp_t)**2)
    k, ps, N = powspec(dens, cell_size)
    k, ps_t, N = powspec(dens_t, cell_size)
    loss += 0.1 * jnp.sum((ps / ps_t - 1)**2)
    return loss


def _loss_norm_power(f, g, spacing=1, log=True, w=None):
    # f (model) & g (target) are fields of the same shape in configuration space
    k, P_d, N, bins = powspec(f - g, spacing, w=w)
    k, P_g, N, bins = powspec(g, spacing)
    loss = (P_d / P_g).sum() / len(k)
    if log: loss = jnp.log(loss)
    return loss


def _loss_log_power(f, g, spacing=1):
    k, P_d, N, bins = powspec(f - g, spacing)
    loss = jnp.log(P_d).sum() / len(k)
    return loss


def _loss_abs_power(f, g, spacing=1, cc_pow=1):
    k, tf, cc = power_tfcc(f, g, spacing)
    return (jnp.abs(1 - tf) + (1 - cc)**cc_pow).sum()


def loss_func(ptcl, tgt, conf, loss_mesh_shape=1):

    # get the target ptcl
    pos_t, vel_t = tgt
    disp_t = pos_t - ptcl.pmid * conf.cell_size
    ptcl_t = Particles(ptcl.conf, ptcl.pmid, disp_t, vel_t)

    # get the disp from particles' grid Lagrangian positions
    # not necessary as long as disp and disp_t have the same reference mesh
    # disp, disp_t = (ptcl_rpos(p, Particles.gen_grid(p.conf), p.conf)
    #                 for p in (ptcl, ptcl_t))
    disp = ptcl.disp
    # reshape -> last 3 axes are spatial dims
    shape_ = (-1,) + conf.ptcl_grid_shape
    disp = disp.T.reshape(shape_)
    disp_t = disp_t.T.reshape(shape_)
    # disp_k = jnp.fft.rfftn(disp, axes=range(-3, 0))
    # disp_t_k = jnp.fft.rfftn(disp_t, axes=range(-3, 0))
    # kvec_disp = rfftnfreq(conf.ptcl_grid_shape, conf.ptcl_spacing,
    # dtype=conf.float_dtype)

    # get the density fields
    (dens, dens_t), (loss_mesh_shape, cell_size) = ptcl2dens(
                                        (ptcl, ptcl_t), conf, loss_mesh_shape)
    # dens_k = jnp.fft.rfftn(dens)
    # dens_t_k = jnp.fft.rfftn(dens_t)
    # kvec_dens = rfftnfreq(loss_mesh_shape, cell_size, dtype=conf.float_dtype)

    loss = 0.

    # displacement
    loss += _loss_mse(disp, disp_t)
    # loss += _loss_mse(disp_k, disp_t_k)
    # loss += _loss_scale_wmse(kvec_disp, disp_k, disp_t_k, 0.5)
    # loss += _loss_norm_power(disp, disp_t)
    # loss += _loss_abs_power(disp, disp_t)

    # density field
    # loss += _loss_mse(dens, dens_t)
    # loss += _loss_mse(dens_k, dens_t_k)
    # loss += _loss_scale_wmse(kvec_dens, dens_k, dens_t_k, 0.5)
    # loss += _loss_abs_power(dens, dens_t)
    loss += _loss_norm_power(dens, dens_t)
    # loss += _loss_log_power(dens, dens_t)

    # velocity
    # loss += _loss_log_mse(ptcl.vel, ptcl_t.vel)

    # other combinations
    # loss += _loss_tfcc(dens, dens_t, cell_size)
    # loss += _loss_Lanzieri(disp, disp_t, dens, dens_t, cell_size)

    return loss
