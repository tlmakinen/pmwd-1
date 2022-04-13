from functools import partial
from itertools import permutations, combinations

from jax import jit, custom_vjp, checkpoint, ensure_compile_time_eval
from jax import random
import jax.numpy as jnp

from pmwd.particles import gen_ptcl
from pmwd.cosmology import E2
from pmwd.boltzmann import growth, linear_power
from pmwd.gravity import rfftnfreq, laplace, neg_grad


#TODO follow pmesh to fill the modes in Fourier space
@partial(jit, static_argnames=('fix_amp', 'negate'))
def white_noise(seed, conf, fix_amp=False, negate=False):
    """White noise Fourier modes.

    Parameters
    ----------
    seed : int
        Seed for the pseudo-random number generator.
    conf : Configuration
    fix_amp : bool, optional
        Whether to fix the amplitudes to 1.
    negate : bool, optional
        Whether to reverse the signs (180° phase flips).

    Returns
    -------
    modes : jax.numpy.ndarray of cosmo.conf.float_dtype
        White noise Fourier modes.

    """
    key = random.PRNGKey(seed)

    # sample linear modes on Lagrangian particle grid
    modes = random.normal(key, conf.ptcl_grid_shape, dtype=conf.float_dtype)

    modes = jnp.fft.rfftn(modes, norm='ortho')

    if fix_amp:
        modes /= jnp.abs(modes)

    if negate:
        modes = -modes

    return modes


@custom_vjp
def _safe_sqrt(x):
    return jnp.sqrt(x)

def _safe_sqrt_fwd(x):
    y = _safe_sqrt(x)
    return y, y

def _safe_sqrt_bwd(y, y_cot):
    x_cot = jnp.where(y != 0, 0.5 / y * y_cot, 0)
    return (x_cot,)

_safe_sqrt.defvjp(_safe_sqrt_fwd, _safe_sqrt_bwd)


def linear_modes(kvec, a, modes, cosmo):
    """Linear matter density field Fourier modes.

    Parameters
    ----------
    kvec : sequence of jax.numpy.ndarray
        Wavevectors.
    a : float or None
        Scale factors. If None, output is not scaled by growth.
    modes : jax.numpy.ndarray
        Fourier modes with white noise prior.
    cosmo : Cosmology

    Returns
    -------
    modes : jax.numpy.ndarray of cosmo.conf.float_dtype
        Linear matter density field Fourier modes in [L^3].

    """
    k = jnp.sqrt(sum(k**2 for k in kvec))

    Plin = linear_power(k, a, cosmo)

    modes *= _safe_sqrt(Plin * cosmo.conf.box_vol)

    return modes


def _strain(k_i, k_j, pot, conf):
    """LPT strain component sourced by scalar potential only."""
    nyquist = jnp.pi / conf.ptcl_spacing
    eps = nyquist * jnp.finfo(conf.float_dtype).eps

    k_i = jnp.where(jnp.abs(jnp.abs(k_i) - nyquist) <= eps, 0, k_i)
    k_j = jnp.where(jnp.abs(jnp.abs(k_j) - nyquist) <= eps, 0, k_j)

    strain = -k_i * k_j * pot

    strain = jnp.fft.irfftn(strain, s=conf.ptcl_grid_shape)
    strain = strain.astype(conf.float_dtype)  # no jnp.complex32

    return strain


def _L(kvec, pot_m, pot_n, conf):
    m_eq_n = pot_n is None
    if m_eq_n:
        pot_n = pot_m

    L = jnp.zeros(conf.ptcl_grid_shape, dtype=conf.float_dtype)

    for i in range(conf.dim):
        strain_m = _strain(kvec[i], kvec[i], pot_m, conf)

        for j in range(conf.dim-1, i, -1):
            strain_n = _strain(kvec[j], kvec[j], pot_n, conf)

            L += strain_m * strain_n

        if not m_eq_n:
            for j in range(i-1, -1, -1):
                strain_n = _strain(kvec[j], kvec[j], pot_n, conf)

                L += strain_m * strain_n

    if not m_eq_n:
        L *= 0.5

    # Assuming strain sourced by scalar potential only, symmetric about ``i`` and ``j``,
    # for lpt_order <=3, i.e., m, n <= 2
    for i in range(conf.dim-1):
        for j in range(i+1, conf.dim):
            strain_m = _strain(kvec[i], kvec[j], pot_m, conf)

            strain_n = strain_m
            if not m_eq_n:
                strain_n = _strain(kvec[j], kvec[i], pot_n, conf)

            L -= strain_m * strain_n

    return L


def levi_civita(indices):
    """Levi-Civita symbol in n-D.

    Parameters
    ----------
    indices : array_like

    Returns
    -------
    epsilon : int
        Levi-Civita symbol value.

    """
    indices = jnp.asarray(indices)

    dim = len(indices)
    lohi = tuple(combinations(range(dim), r=2))
    lohi = jnp.array(lohi).T
    lo, hi = lohi[0], lohi[1]  # https://github.com/google/jax/issues/1583

    epsilon = jnp.sign(indices[hi] - indices[lo]).prod()

    return epsilon.item()


def _M(kvec, pot, conf):
    M = jnp.zeros(conf.ptcl_grid_shape, dtype=conf.float_dtype)

    for indices in permutations(range(conf.dim), r=3):
        i, j, k = indices
        strain_0i = _strain(kvec[0], kvec[i], pot, conf)
        strain_1j = _strain(kvec[1], kvec[j], pot, conf)
        strain_2k = _strain(kvec[2], kvec[k], pot, conf)

        with ensure_compile_time_eval():
            epsilon = levi_civita(indices)
        M += epsilon * strain_0i * strain_1j * strain_2k

    return M


@jit
@checkpoint
def lpt(modes, cosmo):
    """Lagrangian perturbation theory at ``cosmo.conf.lpt_order``.

    Parameters
    ----------
    modes : jax.numpy.ndarray
        Fourier modes with white noise prior.
    cosmo : Cosmology

    Returns
    -------
    ptcl : Particles
    obsvbl : Observables

    Raises
    ------
    ValueError
        If ``cosmo.conf.dim`` or ``cosmo.conf.lpt_order`` is not supported.

    """
    conf = cosmo.conf

    if conf.dim not in (1, 2, 3):
        raise ValueError(f'dim={conf.dim} not supported')
    if conf.lpt_order not in (0, 1, 2, 3):
        raise ValueError(f'lpt_order={conf.lpt_order} not supported')

    kvec = rfftnfreq(conf.ptcl_grid_shape, conf.ptcl_spacing, dtype=conf.float_dtype)

    modes = linear_modes(kvec, None, modes, cosmo)  # not scaled by growth
    modes *= 1 / conf.ptcl_cell_vol  # remove volume factor first for convenience

    pot = []

    if conf.lpt_order > 0:
        src_1 = modes

        pot_1 = laplace(kvec, src_1, cosmo)
        pot.append(pot_1)

    if conf.lpt_order > 1:
        src_2 = _L(kvec, pot_1, None, conf)

        src_2 = jnp.fft.rfftn(src_2)

        pot_2 = laplace(kvec, src_2, cosmo)
        pot.append(pot_2)

    if conf.lpt_order > 2:
        raise NotImplementedError('TODO')

    a = conf.a_start
    ptcl = gen_ptcl(conf)

    for order in range(1, 1+conf.lpt_order):
        D = growth(a, cosmo, order=order).astype(conf.float_dtype)
        dD_dlna = growth(a, cosmo, order=order, deriv=1).astype(conf.float_dtype)
        a2HDp = a**2 * jnp.sqrt(E2(a, cosmo)) * dD_dlna

        for i, k in enumerate(kvec):
            grad = neg_grad(k, pot[order-1], conf.ptcl_spacing)

            grad = jnp.fft.irfftn(grad, s=conf.ptcl_grid_shape)
            grad = grad.astype(conf.float_dtype)  # no jnp.complex32

            grad = grad.ravel()

            disp = ptcl.disp.at[:, i].add(D * grad)
            vel = ptcl.vel.at[:, i].add(a2HDp * grad)
            ptcl = ptcl.replace(disp=disp, vel=vel)

    obsvbl = None  # TODO cubic Hermite interp lightcone as in nbody

    return ptcl, None
