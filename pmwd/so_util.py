import jax
import jax.numpy as jnp
from jax import random, vmap
from jax.tree_util import tree_map
import flax.linen as nn
from flax.core.frozen_dict import unfreeze, freeze
from typing import Sequence, Callable
import math

from pmwd import (
    Configuration,
    SimpleLCDM,
    boltzmann,
    H_deriv,
    Omega_m_a,
    growth,
    linear_power,
)


class MLP(nn.Module):
    features: Sequence[int]
    activator: Callable[[jnp.ndarray], jnp.ndarray] = nn.softplus
    outivator: Callable[[jnp.ndarray], jnp.ndarray] = None

    def setup(self):
        self.layers = [nn.Dense(f) for f in self.features]

    def __call__(self, inputs):
        x = inputs
        for i, lyr in enumerate(self.layers):
            x = lyr(x)
            if i != len(self.layers)-1:
                x = self.activator(x)
            else:
                if self.outivator is not None:
                    x = self.outivator(x)

        return x


def init_mlp_params(n_input, nodes, scheme=None):
    """Initialize MLP parameters."""
    nets = [MLP(features=n) for n in nodes]
    xs = [jnp.ones(n) for n in n_input]  # dummy inputs
    keys = random.split(random.PRNGKey(0), len(n_input))

    # by default in flax.linen.Dense, kernel: lecun_norm, bias: 0
    params = [nn.init(key, x) for nn, key, x in zip(nets, keys, xs)]

    # for the last layer: set weights to zero and bias to one
    # TODO instead of zero, use very small random values for weights?
    if scheme == 'last_w0_b1':
        for i, p in enumerate(params):
            p = unfreeze(p)
            p['params'][f'layers_{len(nodes[i])-1}']['kernel'] = (
                jnp.zeros((nodes[i][-2], nodes[i][-1])))
            p['params'][f'layers_{len(nodes[i])-1}']['bias'] = (
                jnp.ones(nodes[i][-1]))
            params[i] = freeze(p)

    return params


def nonlinear_scales(cosmo, conf, a):
    k = conf.transfer_k[1:]
    D = growth(a, cosmo, conf)
    dD = growth(a, cosmo, conf, deriv=1)
    dD2i = -2 * D**(-3) * dD  # d(1/D^2) / dlna
    interp_valgrad = jax.value_and_grad(jnp.interp, argnums=0)

    # dimensionless linear power
    Plin = linear_power(k, None, cosmo, conf)  # no a dependence
    k_P, dk_P = interp_valgrad(1 / D**2, k**3 * Plin / (2 * jnp.pi**2), k)
    dk_P *= dD2i

    # TopHat variance, var is decreasing with R
    # but for jnp.interp, xp must be increasing, thus the reverse [::-1]
    R_TH, dR_TH = interp_valgrad(1 / D**2, cosmo.varlin[::-1], conf.varlin_R[::-1])
    dR_TH *= dD2i

    # Gaussian variance
    R_G, dR_G = interp_valgrad(1 / D**2, cosmo.varlin_g[::-1], conf.varlin_R_g[::-1])
    dR_G *= dD2i

    # rms linear theory displacement
    Rd = (jnp.trapz(k * Plin, x=jnp.log(k)) + k[0] * Plin[0] / 2) / (6 * jnp.pi**2)
    Rd = jnp.sqrt(Rd)
    dRd = Rd * dD
    Rd *= D

    return (1/k_P, R_TH, R_G, Rd, -dk_P/k_P**2, dR_TH, dR_G, dRd)


def sotheta(cosmo, conf, a):
    # quantities of dim L
    theta_l = jnp.asarray([
        *nonlinear_scales(cosmo, conf, a),
        conf.ptcl_spacing,
        conf.cell_size,
    ])
    if conf.softening_length is not None:
        theta_l = jnp.append(theta_l, conf.softening_length)

    # dimensionless quantities
    D1 = growth(a, cosmo, conf, order=1)
    dlnD1 = growth(a, cosmo, conf, order=1, deriv=1) / D1
    D2 = growth(a, cosmo, conf, order=2)
    dlnD2 = growth(a, cosmo, conf, order=2, deriv=1) / D2
    theta_o = jnp.asarray([
        D1 / a,
        D2 / a**2,
        dlnD1 - 1,
        dlnD2 - 2,
        Omega_m_a(a, cosmo),
        H_deriv(a, cosmo),
        # time step size?
    ])

    return (theta_l, theta_o)


def soft_len():
    # get the length of SO input features with dummy conf and cosmo
    conf = Configuration(1., (128,)*3)
    cosmo = SimpleLCDM(conf)
    cosmo = boltzmann(cosmo, conf)
    tl, to = sotheta(cosmo, conf, conf.a_start)
    return len(tl) + len(to)


def soft_bc(k, theta):
    """SO features for neural nets input, broadcast with k being an array."""
    # multiply each element of k with theta_l, and append theta_o
    theta_l, theta_o = theta
    k_shape = k.shape
    ft = k.reshape(k_shape+(1,)) * theta_l.reshape((1,)*len(k_shape)+theta_l.shape)
    ft = jnp.concatenate((ft, jnp.broadcast_to(theta_o, k_shape+theta_o.shape)), axis=-1)
    return ft


def sonn_bc(k, theta, cosmo, conf, nid):
    """Evaluate the neural net, broadcast with k being processed in one pass."""
    ft = soft_bc(k, theta)
    net = MLP(features=conf.so_nodes[nid])
    return net.apply(cosmo.so_params[nid], ft)[..., 0]  # rm the trailing axis of dim one


def soft(k, theta):
    """SO features for neural nets input, with k being a scalar."""
    theta_l, theta_o = theta
    return jnp.concatenate((k * theta_l, theta_o))


def sonn_vmap(k, theta, cosmo, conf, nid):
    """Evaluate the neural net, using vmap over k."""
    net = MLP(features=conf.so_nodes[nid])
    def _sonn(_k):
        _ft = soft(_k, theta)
        return net.apply(cosmo.so_params[nid], _ft)[0]
    return vmap(_sonn)(k.ravel()).reshape(k.shape)


def pot_sharp(kvec, theta, pot, cosmo, conf, a):
    """Spatial optimization of the laplace potential."""
    f = [sonn_bc(k_, theta, cosmo, conf, 0) for k_ in kvec]

    k = jnp.sqrt(sum(k_**2 for k_ in kvec))
    g = sonn_bc(k, theta, cosmo, conf, 1)
    # ks = jnp.array_split(k, 16)
    # g = []
    # for k in ks:
    #     g.append(sonn_vmap(k, theta, cosmo, conf, 1))
    # g = jnp.concatenate(g, axis=0)

    pot *= g * math.prod(f)
    return pot


def grad_sharp(k, theta, grad, cosmo, conf, a):
    """Spatial optimization of the gradient."""
    grad *= sonn_vmap(k, theta, cosmo, conf, 2)
    return grad
