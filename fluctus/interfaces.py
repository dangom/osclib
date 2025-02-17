"""
This provides a high-level API for dealing with oscillations
The idea is that a user provides either a time-series or collection of
time-series, and we provide objects for conveniently manipulating them.

By chaining transforms, one can easily generate any analysis of interest.
"""
from dataclasses import dataclass
from typing import Optional, Union

import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from nilearn.maskers import NiftiMasker, NiftiLabelsMasker
from scipy.signal import butter, sosfiltfilt
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import MinMaxScaler, StandardScaler, RobustScaler
from sklearn.exceptions import NotFittedError
from sklearn.utils.validation import check_is_fitted

from fluctus import preprocessing


# Helpers
def get_ntrials(start_offset, period, tr, nvols):
    source_grid_ = [x * tr for x in range(nvols)]
    total_time = source_grid_[-1]
    trials, _ = divmod(total_time - start_offset, period)
    return trials


def get_offset(stimulus_offset=14, period=10, discard_transients=True):
    # Discard transients is a toggle that automatically adds 40 seconds to the
    # offset to account for non steady-state effects of continuous stimulation.
    # TODO remove this toggle and have callers add it automatically.
    offset = stimulus_offset
    # 39.999 just so we accept 54 as valid. Python doesn't have a do while loop.
    if period is not None and (stimulus_offset < 40) and discard_transients:
        while offset <= (stimulus_offset + 39.999):  # 40 seconds after start of stim
            offset += period
    return offset


def correlate(a: np.array, b: np.array) -> np.array:
    """Fast numpy Row-wise Corr. Coefficients
    See benchmarks at https://stackoverflow.com/a/30143754/3568092
    Correlates rows of first and second arrays, which may be either 1 or 2D.
    """

    # If one of the inputs is 1D we will set this to True and squeeze the final
    # result to return a 1D vector.
    need_squeeze: bool = False

    if a.ndim == 1:
        need_squeeze = True
        a = a[np.newaxis, :]

    if b.ndim == 1:
        need_squeeze = True
        b = b[np.newaxis, :]

    # Center vectors by subtracting row mean.
    a_centered: np.array = a - a.mean(axis=1)[:, None]
    b_centered: np.array = b - b.mean(axis=1)[:, None]

    # Sum of squares across rows.
    a_sos: np.array = (a_centered ** 2).sum(axis=1)
    b_sos: np.array = (b_centered ** 2).sum(axis=1)

    norm_factors: np.array = np.sqrt(np.dot(a_sos[:, None], b_sos[None]))
    corr = np.dot(a_centered, b_centered.T) / norm_factors

    return corr if not need_squeeze else corr.squeeze()


def scale(x):
    return StandardScaler().fit_transform(x.reshape(-1, 1)).ravel()


def find_delay(arr, reference, maxshift=10, tr=0.1, scaleref:bool=True, rect_data:bool =False):
    a_scaled = StandardScaler().fit_transform(arr)
    a_scaled = a_scaled * (a_scaled > 0) if rect_data else a_scaled
    b_scaled = reference if not scaleref else scale(reference)
    corrs = np.array([correlate(a_scaled.T, np.roll(b_scaled, -x)) for x in range(-maxshift, maxshift)])
    delay = np.array(list(range(-maxshift, maxshift)))[np.argmax(corrs, 0)]
    return  - (delay * tr)  # invert because we want delay in arr wrt to ref, not the other way around


@dataclass
class Oscillation:
    """
    A class to represent an oscillation and offer methods to trial-average, voxel-average,
    PSC normalization, and FFT.
    Also offers a method to extract the time-series of a given mask and/or label from nifti.
    """
    tr: float
    period: float
    data: np.array
    stimulus_offset: float = 14
    labels: Optional[list] = None
    discard_transients: bool = True

    def __post_init__(self):
        self.tdata = self.data
        self.transformation_chain = []
        self.sampling_rate = self.tr
        self.grid = np.arange(self.data.shape[0]) * self.sampling_rate
        self.offset = get_offset(self.stimulus_offset, self.period, self.discard_transients)
        self.n_trials = get_ntrials(
            self.offset, self.period, self.tr, self.data.shape[0]
        )
        self.emin, self.emax = None, None
        self.ids = [""]

    def reset(self):
        self.tdata = self.data
        self.transformation_chain = []
        self.sampling_rate = self.tr
        self.grid = np.arange(self.data.shape[0]) * self.sampling_rate
        self.emin, self.emax = None, None

    def clear_labels(self):
        self.labels = None

    def _transform(self, transformer, id: str):
        try:
            check_is_fitted(transformer)
        except NotFittedError:
            transformer.fit(self.tdata)
        self.tdata = transformer.transform(self.tdata)
        self.transformation_chain.append(id)
        return self

    def average(self):
        if self.labels is None:
            transformer = preprocessing.FeatureAverager()
            self.ids = [""]
        else:
            label_ids = set(self.labels)
            transforms = []
            for id in label_ids:
                transforms.append(
                    (
                        id,
                        preprocessing.FeatureAverager(),
                        np.where(np.array(self.labels) == id)[0],
                    )
                )
            transformer = ColumnTransformer(transforms)
            self.ids = label_ids
        return self._transform(transformer, "Label Average")

    def psc(self, baseline_begin: float = 0., baseline_end: float = np.inf):
        # Never PSC twice, because that would mess up amplitudes.
        if "PSC" in self.transformation_chain:
            return self
        transformer = preprocessing.PSCScaler()
        # PSC scale only on the first 10 seconds.
        begin_vol = int(baseline_begin / self.sampling_rate)
        try:
            end_vol = int(baseline_end / self.sampling_rate)
            end_vol = min(end_vol, self.tdata.shape[0])
        except (ZeroDivisionError, OverflowError):
            end_vol = self.tdata.shape[0]
        transformer.fit(self.tdata[begin_vol: end_vol, :])
        return self._transform(transformer, "PSC")

    def detrend(self, order:int = 3, keep_mean:bool = True):
        if "Detrend" in self.transformation_chain:
            return self
        transformer = preprocessing.Detrender(order)
        if keep_mean:
            mean = self.tdata.mean(0)
        transformed =  self._transform(transformer, "Detrend")
        if keep_mean:
            self.tdata += mean
        return transformed

    def trial_average(self, bootstrap: bool = False):
        transformer = preprocessing.TrialAveragingTransformer(
            n_trials=self.n_trials, bootstrap=bootstrap
        )
        transformed = self._transform(transformer, "Trial Average")
        if bootstrap:
            self.emin, self.emax = transformer.ci_low_, transformer.ci_high_
        self.grid = self.grid[: self.tdata.shape[0]]
        self.sampling_rate = self.grid[1] - self.grid[0]
        return transformed

    def interp(self, target_sampling_out: float = 0.1):
        transformer = preprocessing.PeriodicGridTransformer(
            period=self.period,
            sampling_in=self.tr,
            target_sampling_out=target_sampling_out,
            start_offset=self.offset,
        )
        transformed = self._transform(transformer, "Crop and Interpolate")
        self.grid = transformer.target_grid_ - self.offset
        self.sampling_rate = self.grid[1] - self.grid[0]
        return transformed

    def fft(self):
        transformer = preprocessing.FFTTransformer(self.sampling_rate)
        transformed =  self._transform(transformer, "FFT")
        # This grid now in Hz
        self.grid = transformer.freqs_
        self.sampling_rate = self.grid[1] - self.grid[0]
        return transformed

    def preprocess(self):
        self.reset()
        self.interp().psc(0, np.inf).average().trial_average(bootstrap=True)
        return self.tdata.squeeze()

    @classmethod
    def from_nifti(
        cls, mask: Union[str, nib.nifti1.Nifti1Image],
            data: Union[str, nib.nifti1.Nifti1Image], period: float, labels=None, stimulus_offset=14, discard_transients=True
    ):
        masker = NiftiMasker(mask_img=mask, verbose=False)
        # If data is str:
        if isinstance(data, str):
            dat = nib.load(data)
        else:
            dat = data
        ts = masker.fit_transform(data)
        init = cls(
            tr=dat.header["pixdim"][4],
            period=period,
            data=ts,
            labels=labels,
            stimulus_offset=stimulus_offset,
            discard_transients=discard_transients,
        )
        init._masker = masker
        init._filename = data
        init._maskname = mask
        return init


    @classmethod
    def from_nifti_labelled(
        cls, mask: Union[str, nib.nifti1.Nifti1Image],
            data: Union[str, nib.nifti1.Nifti1Image], period: float, labels=None, stimulus_offset=14, discard_transients=True
    ):
        masker = NiftiLabelsMasker(labels_img=mask, labels=labels)
        # If data is str:
        if isinstance(data, str):
            dat = nib.load(data)
        else:
            dat = data
        ts = masker.fit_transform(data)
        init = cls(
            tr=dat.header["pixdim"][4],
            period=period,
            data=ts,
            labels=labels,
            stimulus_offset=stimulus_offset,
            discard_transients=discard_transients,
        )
        init._masker = masker
        init._filename = data
        init._maskname = mask
        init.transformation_chain = ["Label Average"]
        init.ids = labels
        return init

    @property
    def phase(self):
        return self.grid[self.tdata.argmin(0)]

    @property
    def transformed_data(self):
        "For backwards compatibility"
        return self.tdata

    def get_crosscorr(self, reference: Optional[np.array] = None, scaleref:bool=True, rect_data:bool =False):
        if reference is None:
            reference = self.tdata.mean(1)
        grid_tr = (self.grid[1] - self.grid[0])
        # Return delay in up to +- 5 seconds
        return find_delay(self.tdata, reference, maxshift=int(5 / grid_tr) , tr=grid_tr, scaleref=scaleref, rect_data=rect_data)

    @property
    def amplitude(self):
        min_max = MinMaxScaler()
        min_max.fit_transform(self.tdata)
        ymin, ymax = min_max.data_min_, min_max.data_max_
        xmin, xmax = (
            self.grid[self.tdata.argmin(0)],
            self.grid[self.tdata.argmax(0)],
        )
        return ymax - ymin

    @property
    def robust_amplitude(self):
        min_max = RobustScaler(quantile_range=(10, 90))
        min_max.fit(self.tdata)
        return min_max.scale_

    def inverse_transform(self, what):
        return self._masker.inverse_transform(what)

    def plot(self, plotci: bool = True):
        fig, ax = plt.subplots(dpi=150)
        # May have to deal with multiple curves, so...
        for i, label in enumerate(self.ids):
            ax.plot(
                self.grid[: self.tdata.size],
                self.tdata[:, i].mean(1),
                label=label,
            )
            if self.emin is not None and plotci:
                ax.fill_between(
                    self.grid[: self.tdata.size],
                    self.emin[:, i],
                    self.emax[:, i],
                    alpha=0.4,
                )

        if "PSC" in self.transformation_chain:
            ylabel = "BOLD % Amplitude"
        else:
            ylabel = "Amplitude"

        ax.set(ylabel=ylabel, xlabel="Time (s)")

        if len(self.ids) > 1:
            ax.legend()

        return fig, ax


## Below are some helper functions to build the confidence index from Regan.


def moving_average(a, n=3):
    ret = np.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1 :] / n


def ratio(trace):
    aplus = np.sum(np.where(trace > 0, trace, 0))
    aminus = np.sum(np.where(trace < 0, trace, 0))
    ratio = np.abs((aplus + aminus) / (aplus - aminus))
    return ratio


def confidence_index(trace_s, trace_c):
    ratio_s = ratio(trace_s)
    ratio_c = ratio(trace_c)
    A = np.sum(trace_s)
    B = np.sum(trace_c)
    amp = np.hypot(A, B)
    conf = (A ** 2 * ratio_s + B ** 2 * ratio_c) / amp ** 2
    return conf


# Since the stimulus starts at 0 and increases, the phase needs an extra np.pi
def phase_estimate(trace_s, trace_c):
    ph = np.arctan2(np.mean(trace_s), np.mean(trace_c))
    return ph


def amplitude_estimate(trace_s, trace_c):
    return np.hypot(np.mean(trace_s), np.mean(trace_c)) / 2


def make_traces(signals, frequency, tr, offset=14, window=50):
    sos = butter(4, frequency / 2, output="sos", fs=1 / tr)
    t = np.array([x * tr for x in range(signals.shape[0])])
    s = np.sin(
        2 * np.pi * frequency * (t - offset + tr / 2)
    )  # adding tr/2 to account for slice time
    c = np.cos(2 * np.pi * frequency * (t - offset + tr / 2))

    multiplier_s = signals * s
    multiplier_c = signals * c
    trace_s = moving_average(sosfiltfilt(sos, multiplier_s), window)[window:-window]
    trace_c = moving_average(sosfiltfilt(sos, multiplier_c), window)[window:-window]
    return trace_s, trace_c


# SLOW BUT NOT BUGGY
def confidence_and_estimates(signals, frequency, tr):
    traces = [
        make_traces(signals[:, x], frequency, tr) for x in range(signals.shape[-1])
    ]
    phase_delays = [
        np.rad2deg(phase_estimate(*trace) + np.pi) * (1 / frequency) / 360
        for trace in traces
    ]
    amps = [amplitude_estimate(*trace) for trace in traces]
    confs = [confidence_index(*trace) for trace in traces]
    return np.array(phase_delays), np.array(amps), np.array(confs)
