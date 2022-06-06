import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import FuncNorm
try:
    shell = get_ipython().__class__.__name__
    if shell == 'ZMQInteractiveShell':
        import matplotlib_inline
        matplotlib_inline.backend_inline.set_matplotlib_formats('jpeg')
except NameError:
    pass  # if not plotting in Jupyter


def simshow(x, figsize=(9, 7), dpi=72, cmap='inferno', norm=None, colorbar=True,
            interpolation='lanczos', interpolation_stage='rgba', **kwargs):
    """Plot a 2D view of simulation with ``imshow``.

    Parameters
    ----------
    x : array_like
        2D field.
    figsize : 2-tuple of float, optional
        Width and height in inches.
    dpi : float, optional
        Figure resolution in dots-per-inch.
    cmap : str or ``matplotlib.colors.Colormap``, optional
        For ``matplotlib.axes.Axes.imshow``.
    norm : 'CosmicWebNorm' or ``matplotlib.colors.Normalize``, optional
        For ``matplotlib.axes.Axes.imshow``.
    colorbar : bool
        Whether to add a colorbar.
    interpolation : str, optional
        For ``matplotlib.axes.Axes.imshow``.
    interpolation_stage : {'data', 'rgba'}, optional
        For ``matplotlib.axes.Axes.imshow``. Unlike in ``imshow``, the default is
        'rgba'.
    **kwargs :
        Other keyword arguments to be passed to ``matplotlib.axes.Axes.imshow``.

    Returns
    -------
    fig : ``matplotlib.figure.Figure``
    ax : ``matplotlib.axes.Axes``

    """
    x = np.asarray(x)

    if norm == 'CosmicWebNorm':
        norm = CosmicWebNorm(x)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

    im = ax.imshow(
        x,
        cmap=cmap,
        norm=norm,
        interpolation=interpolation,
        interpolation_stage=interpolation_stage,
        **kwargs,
    )
    ax.set_axis_off()

    if colorbar:
        cb = fig.colorbar(im)
        try:
            ticks, ticklabels = norm.get_colorbar_ticks()  # for CosmicWebNorm
            cb.set_ticks(ticks, labels=ticklabels)
        except AttributeError:
            pass
        cb.ax.tick_params(which='both', length=0,
                          labelsize=figsize[1] * 72 / 36, labelcolor='grey')
        cb.outline.set_visible(False)

    return fig, ax


class CosmicWebNorm(FuncNorm):
    """Colormap normalization for cosmic web (relative) density fields.

    Use ``plot()`` to look at the normalization transformations.

    Parameters
    ----------
    x : array_like
        Density field.
    q : float, optional
        Underdensity fraction in colormap.
    gamma : float, optional
        Overdensity contrast. Larger value gives lower contrast.
    fit_min : float, optional
        Minimum density for cumulative histogram and CCDF fitting. ``x.min()`` is used
        if it's larger than this.
    fit_num : int, optional
        Number of points for cumulative histogram and CCDF fitting.
    clip : bool, optional
        For ``matplotlib.colors.FuncNorm``.

    Attributes
    ----------
    a : float
        CCDF scale parameter that divides the power law and the exponential tail.
    b : float
        CCDF scale parameter that divides the underdensity and power law.
    alpha : float
        CCDF shape parameter of the exponential tail.
    beta : float
        CCDF shape parameter of the power law.
    theta : float
        Underdensity contrast. Larger value gives lower contrast. It is determined by
        ``q`` and ``gamma`` in a way that maps density ``b`` to colormap value ``q``.

    """
    def __init__(self, x, q=0.1, gamma=0.5, fit_min=1e-2, fit_num=64, clip=False):
        if not 0 < q < 1:
            raise ValueError(f'q = {q} not in (0, 1)')
        if gamma <= 0:
            raise ValueError(f'gamma = {gamma} <= 0')

        x = np.asarray(x)

        self.fit_min = max(fit_min, x.min())
        self.max = x.max()
        self.q = q
        self.gamma = gamma

        self.bins, self.hist = self.cumhist(x, fit_num)

        self.a, self.b, self.alpha, self.beta = self.fit()

        self.theta = np.log(q) / np.log(1 - self.ccdf(self.b) ** gamma)

        super().__init__((self.forward_, self.inverse_), clip=clip)

    def cumhist(self, x, num):
        """Cumulative histogram of density above the bin edge values."""
        bins = np.logspace(np.log10(self.fit_min), np.log10(self.max), num=num+1,
                           endpoint=True)
        bins[-1] *= 2  # make sure the maximum is in the last bin

        hist, _ = np.histogram(x, bins)
        hist = np.cumsum(hist[::-1])[::-1]
        hist = hist / hist[0]  # ignore the huge pile at/near zero

        return bins[:-1], hist

    def ccdf(self, x, a=None, b=None, alpha=None, beta=None):
        """Fitting function for density complementary cumulative distribution function.

        This is inspired by the fact that the overdense side of the CCDF (and PDF) looks
        like a power law followed by an exponential tail.

        """
        if a is None:
            a = self.a
        if b is None:
            b = self.b
        if alpha is None:
            alpha = self.alpha
        if beta is None:
            beta = self.beta

        return np.exp(- (x / a) ** alpha) / (1 + (x / b) ** beta)

    def corr(self, p, gamma=None, theta=None):
        """Correct/map density CCDF to colormap values in [0, 1].

        This uses CCDF of the Kumaraswamy distribution. ``gamma`` and ``theta`` control
        the contrast at the overdense and underdense ends, respectively. Larger values
        give lower contrast. ``theta`` is determined by ``q`` and ``gamma`` in a way
        that maps density ``b`` to colormap value ``q``.

        """
        if gamma is None:
            gamma = self.gamma
        if theta is None:
            theta = self.theta

        return (1 - p**gamma) ** theta

    def forward_(self, x):
        """Map densities to colormap values in [0, 1]."""
        return self.corr(self.ccdf(x))

    def inverse_(self, y):
        """Map colormap values in (0, 1) to densities."""
        from scipy.optimize import root_scalar

        return np.array([
            root_scalar(
                lambda x: self.forward_(x) - v,
                method='toms748',
                bracket=[0, 10 * self.max],
            ).root
            for v in y.ravel()
        ]).reshape(y.shape)  # strangely y is 2D

    def fit(self):
        """Fit the density complementary cumulative distribution function."""
        from scipy.optimize import curve_fit

        def lnccdf(x, *p):
            return np.log(self.ccdf(x, *p))

        popt, pcov = curve_fit(
            lnccdf,
            self.bins,
            np.log(self.hist),
            p0=[self.max, self.fit_min, 1, 1],
            bounds=(2 * [self.fit_min] + 2 * [0], 2 * [self.max] + 2 * [np.inf]),
        )

        return popt

    def plot(self):
        """Plot the ``ccdf`` and ``corr`` transformations."""
        fig, (ax0, ax1) = plt.subplots(ncols=2, figsize=(9, 4))

        # CCDF
        ax0.loglog(self.bins, self.hist, label='data')
        ax0.loglog(self.bins, self.ccdf(self.bins), label='fit', ls='--')
        ax0.scatter(self.a, self.ccdf(self.a), c='grey', marker='X', label='$a$')
        ax0.scatter(self.b, self.ccdf(self.b), c='grey', marker='P', label='$b$')
        ax0.set_xlabel('density $x$')
        ax0.set_ylabel('CCDF $p$')
        ax0.set_xlim(self.fit_min, self.max)
        ax0.set_ylim(self.hist[-1], 1)
        ax0.legend()

        # correction
        p = np.linspace(0, 1, num=1001, endpoint=True)
        p_b = self.ccdf(self.b)
        ax1.plot(p, self.corr(p))
        ax1.scatter(p_b, self.corr(p_b), c='grey', marker='P')
        ax1.set_xlabel('CCDF $p$')
        ax1.set_ylabel('cmap $y$')
        ax1.set_xlim(0, 1)
        ax1.set_ylim(0, 1)

        return fig, (ax0, ax1)

    def get_colorbar_ticks(self, subs=(1, 2, 5), sep=0.03):
        """Get pretty colorbar ticks locations and labels."""
        lg_min = int(np.floor(np.log10(self.fit_min)))
        lg_max = int(np.floor(np.log10(self.max)))
        x = np.array([s * 10**e for e in range(lg_min, lg_max+1) for s in subs])
        x = x[(x >= self.fit_min) & (x <= self.max)]

        # only keep well separated ticks in the candidates
        y = self.forward_(x)
        i, = np.nonzero(np.diff(y) > sep)
        i = np.sort(i)
        i = np.append(i, i[-1] + 1)
        ticks = x[i]

        ticklabels = [str(x) for x in ticks]
        try:
            ticklabels = [s.lstrip('0').removesuffix('.0') for s in ticklabels]
        except AttributeError:
            pass  # python < 3.9

        return ticks, ticklabels


def slice_view(pos,
               box_size=None,
               dim=0,
               origin=None,
               start=0.4,
               width=0.2,
               bins=300,
               cmap='viridis',
               save_fig=None,
               dpi=100,
               fsize=10,
               logscale=True):
    """Plot a projected slice view of the particles in a cubic snapshot.

    Parameters
    ----------
    pos : ndarray
        Particle positions array of dimension ``(N, 3)``.
    box_size : float
        Size of the cubic box.
    dim : int
        The dimension along which the slice is made.
    origin : float
        The origin coordinate of the box.
    start : float
        The start position of the slice, given as a fraction of ``box_size``.
    width : float
        The width of the slice, given as a fraction of ``box_size``.
    bins : int
        The number of bins for the projected 2D histogram.
    cmap : str
        Color map for ``imshow``.
    save_fig : str
        The file name where the figure will be saved.
    dpi : int
        Dots per inch for the figure.
    fsize : float
        Size (inch) of the figure.
    logscale : bool
        Plot in log scale or not.
    """
    # range of the cube box
    if not box_size:
        box_size = np.max(np.abs(pos.max(axis=0) - pos.min(axis=0)))
    if not origin:
        origin = pos.min()

    # range of slice
    width *= box_size
    start = origin + start * box_size
    end = start + width

    # generate the projected number of particles
    mask = np.ones(3, dtype=bool)
    mask[dim] = False
    pos = pos[(pos[:, dim] >= start) & (pos[:, dim] <= end)][:, mask]
    hist, edges = np.histogramdd(pos, bins=bins, range=[(origin, origin + box_size)]*2)
    if logscale:
        hist[hist == 0] = np.nan
        hist = np.log10(hist)
        hist[np.isnan(hist)] = -1e-15 # empty pixels

    # plot
    fig, ax = plt.subplots(1, 1, figsize=(fsize, fsize), dpi=dpi)
    ax.imshow(hist, cmap=cmap)
    ax.set_facecolor('k')
    ax.axis('off')

    # save figure
    if save_fig:
        fig.savefig(save_fig, bbox_inches='tight', pad_inches=0)

    return fig, ax
