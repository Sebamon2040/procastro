# dataproc - general data processing routines
#
# Copyright (C) 2013 Patricio Rojo
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of version 2 of the GNU General
# Public License as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor,
# Boston, MA  02110-1301, USA.
#
#

__all__ = ['AstroFile', 'AstroCalib']

import logging
from functools import wraps as _wraps
import warnings
import re

import pandas as pd

import dataproc as dp
import astropy.time as apt
import numpy as np
import os.path as path
import astropy.io.fits as pf
from .astrocommon import AstroCommon

iologger = logging.getLogger('dataproc.io')


#######################
#
# FITS handling of AstroFile
#
##################################


def _fits_reader(filename, hdu=0):
    """
    Read fits files.

    Parameters
    ----------
    hdu : int, optional
        HDU slot to be read, if -1 returns all hdus

    Returns
    -------
    array_like
    """
    if hdu < 0:
        return pf.open(filename)

    hdu_list = pf.open(filename)
    fl = hdu_list[hdu].data
    hdu_list.close()
    return fl


def _fits_writer(filename, data, header=None):
    """
    Writes 'data' to specified file

    Parameters
    ----------
    filename : str
    data : array_like
    header : Header Object, optional
    """
    if header is None:
        header = pf.Header()
        iologger.warning(
            "No header provided to save on disk, using a default empty header"
            "for '{}'".format(filename))
    header['history'] = "Saved by dataproc v{} on {}".format(dp.__version__,
                                                             apt.Time.now())
    return pf.writeto(filename, data, header,
                      overwrite=True, output_verify='silentfix')


def _fits_verify(filename, ffilter=None, hdu=0):
    """
    Verifies that the given file has the expected extensions of fit files
    Accepted extensions: 'fits', 'fit', 'ftsc', 'fts', 'fitsgz',
                         'fitgz', 'fitszip', 'fitzip'

    Parameters
    ----------
    filename : str
    ffilter : dict, list, optional
        If given, the method checks if the file also has the specified values
        on their header
    hdu : int, optional
        HDU to be checked

    Returns
    -------
    bool
    """
    if not isinstance(filename, str):
        return False

    single_extension = filename.lower().split('.')[-1] in ['fits', 'fit',
                                                           'ftsc', 'fts']
    double_extension = ''.join(filename.lower()
                               .split('.')[-2:]) in ['fitsgz', 'fitgz', 'ftsgz']
    if single_extension or double_extension:
        if ffilter is None:
            return True
        h = pf.getheader(filename, hdu)
        if isinstance(ffilter, dict):
            return False not in [(f in h and h[f] == ffilter[f])
                                 for f in ffilter.keys()]
        if isinstance(ffilter, (list, tuple)):
            return False not in [f in h for f in ffilter]
    return False


def _fits_getheader(_filename, *args, **kwargs):
    """
    Obtains data from headers

    Parameters
    ----------
    _filename : str

    Returns
    -------
    list of dicts
        Each dictionary contains the header data from a hdu unit inside
        the file
    """
    ret = []
    with pf.open(_filename) as hdu_list:
        for h in hdu_list:
            ret.append({k.lower(): v for k, v in h.header.items()})

    return ret


def _fits_setheader(_filename, **kwargs):
    """
    Sets value of a header item

    Parameters
    ----------
    _filename : str
    hdu : int, optional
    write : bool, optional
    **kwargs :
        Set of key value pairs containing the data to be read

    Returns
    -------
    bool
        True if succesfull
    """
    hdu = ('hdu' in kwargs and [kwargs['hdu']] or [0])[0]

    if 'write' in kwargs and kwargs['write']:
        save = True
        try:
            # TODO: if file does not exist, create it
            hdulist = pf.open(_filename, 'update')
            fits = hdulist[hdu]
        except IOError:
            iologger.warning(
                "Read-only filesystem or file not found. "
                "Header update of '{0:s}' will not remain "
                "on disk.".format(', '.join(kwargs.keys())))
            return
    else:
        hdulist = pf.open(_filename)
        fits = hdulist[hdu]
        save = False
    if 'write' in kwargs:
        del kwargs['write']

    h = fits.header
    for k, v in kwargs.items():
        # If new value is None, then delete it
        if v is None:
            del h[k]
        else:
            h[k] = v
    if save:
        fits.flush()
    hdulist.close()
    return True

#############################
#
# Startnparray type
#
####################################


def _array_reader(array, **kwargs):
    return array


def _array_write(filename, **kwargs):
    raise NotImplementedError("Write array into file is not implemented yet")


def _array_setheader(filename, **kwargs):
    warnings.warn("Saving an np.array type is not implemented yet: only saving in memory for now ",
                  UserWarning)
    return None


def _array_getheader(array, **kwargs):
    return [{}]


def _array_verify(array, **kwargs):
    if isinstance(array, np.ndarray):
        return True
    return False


#############################
#
# End Defs
#
####################################

def return_none(*args, **kwargs):
    return None


class _AstroDecorator:

    @staticmethod
    def numerize_other(fcn):
        """
        When operating between two AstroFile objects, this decorator will try to
        read the second AstroFile first.
        """

        @_wraps(fcn)
        def wrapper(instance, other, *args, **kwargs):
            if isinstance(other, AstroFile):
                other = other.reader()
            return fcn(instance, other, *args, **kwargs)

        return wrapper

    @staticmethod
    def check_filename(fcn):
        """
        Verifies that the given filename is valid
        """
        @_wraps(fcn)
        def isfiledef(self, *args, **kwargs):
            if hasattr(self, 'filename'):
                if self.type is None:
                    # raise ValueError("File %s not a supported astro-type." % (f))
                    raise ValueError(
                        "Please specify filename with setFilename first.")
                return fcn(self, *args, **kwargs)
            else:
                raise ValueError(
                    "Filename not defined. Must give valid filename to AstroFile")

        return isfiledef

    @staticmethod
    def check_sort_key(fcn):
        """
        Verifies that the given sortkey is valid
        """
        @_wraps(fcn)
        def ret(self, *args, **kwargs):
            if self.sort_key is None:
                raise ValueError(
                    "Sortkey must be defined before trying to sort AstroFile")
            if not isinstance(self.sort_key, str) or "," in self.sort_key:
                raise ValueError(
                    "Invalid value for sortkey ({}), it must be a single header"
                    "specification (without commas)".format(self.sort_key))
            return fcn(self, *args, **kwargs)

        return ret


class AstroFile(AstroCommon):
    """
    Representation of an astronomical data file, contains information related
    to the file's data. Currently supports usage of arrays and .fit files

    Parameters
    ----------
    filename : str or dataproc.AstroFile
        If AstroFile, then it does not duplicate the information,
        but uses that object directly.
    mbias : dict indexed by exposure time, array or AstroFile, optional
        Default Master bias
    mflat : dict indexed by filter name, array or AstroFile, optional
        Default Master flat
    exists: boolean, optional
        Represents if the given pathname has an existing file
    mbias_header : astropy.io.fits.Header
    mflat_header : astropy.io.fits.Header
    hdu : int, optional
        Default hdu slot used for collection, if "hduh" or "hdud" are not
        specified, this parameter will override them.
    hduh : int, optional
        hdu slot containing the header information to be collected
    hdud : int, optional
        hdu slot containing astronomical data to be collected
    read_keywords : list, optional
        Read all specified keywords at creation time and store them in
        the header cache
    auto_trim : str, optional
        Header field name which is used to cut a portion of the data. This
        field should contain the dimensions of the section to be cut.
        (i.e. TRIMSEC = '[1:1992,1:1708]')
    ron : float, str, astropy quantity, optional
        Read-out-noise value or header keyword. Only consulted if either
        mbias or mflat are not CCDData. If unit not specified, photon/ADU
        is used as default unit; if not set at all, uses 1.0 (with warning)
    gain : float, str, astropy quantity, optional
        Gain value or header keyword. Only consulted if either
        mbias or mflat are not CCDData. If unit not specified, photon/ADU
        is used as default unit; if not set at all, uses 1.0 (with warning)
    unit : astropy.unit, optional
        Specifies the unit for the data

    Attributes
    ----------
    shape : tuple
        Shape of the data contained on the file
    header_cache : dict
        Data contained on the most recently read header
    sortkey : str
        Header Item used when sorting this object
    type : str
        File extension

    """

    # Interface between public and private methods for each supported type
    _reads = {'fits': _fits_reader, 'nparray': _array_reader}
    # _readhs = {'fits': _fits_header, 'nparray': array_header}
    _ids = {'fits': _fits_verify, 'nparray': _array_verify}
    _writes = {'fits': _fits_writer, 'nparray': _array_write}
    _geth = {'fits': _fits_getheader, 'nparray': _array_getheader}
    _seth = {'fits': _fits_setheader, 'nparray': _array_setheader}

    def __repr__(self):
        if isinstance(self.filename, str):
            filename = self.filename
        elif isinstance(self.filename, np.ndarray):
            filename = f"Array {'x'.join([str(i) for i in self.filename.shape])}"
        else:
            filename = str(self.filename)
        return '<AstroFile{}: {}>'.format(self.has_calib()
                                          and "({}{})"
                                              .format(self.calib.has_bias
                                                      and "B" or "",
                                                      self.calib.has_flat
                                                      and "F" or "",)
                                          or "",
                                          filename,)

    def __new__(cls, *args, **kwargs):
        """
        If passed an AstroFile, then do not create a new instance, just pass
        that one
        """
        if args and isinstance(args[0], AstroFile):
            return args[0]

        return super(AstroFile, cls).__new__(cls)

    def __init__(self, filename=None,
                 mbias=None, mflat=None,
                 mbias_header=None, mflat_header=None,
                 hdu=0, hduh=None, hdud=None,
                 auto_trim=None, header=None,
                 *args, **kwargs):

        if isinstance(filename, AstroFile):
            return

        self.type = self.checktype(*args, **kwargs)
        self.filename = filename
        self.sort_key = None

        self._info = None
        _ = self.readheader(use_header=header)

        if hduh is None:
            hduh = hdu
        if hdud is None:
            hdud = hdu
        self._hduh = hduh
        self._hdud = hdud

        self.calib = AstroCalib(mbias, mflat,
                                mbias_header=mbias_header,
                                mflat_header=mflat_header,
                                auto_trim=auto_trim)

    def add_bias(self, mbias):
        """
        Includes a Master Bias to this instance

        Parameters
        ----------
        mbias: dict indexed by exposure time, array or AstroFile
        """
        self.calib.add_bias(mbias)

    def add_flat(self, master_flat):
        """
        Includes a Master Bias to this instance

        Parameters
        ----------
        master_flat: dict indexed by filter name time, array or AstroFile
        """
        self.calib.add_flat(master_flat)

    def default_hdu_dh(self):
        """
        Returns default HDU for data and header collection
        """
        return [self._hdud, self._hduh]

    def checktype(self, *args, **kwargs):
        """
        Verifies if the filename given corresponds to an existing file

        Parameters
        ----------

        Returns
        -------
        str
            "array" : If AstroFile represents a scipy array
            "fits" : If AstroFile points to an existing .fits file
            None : Otherwise
        """
        if not hasattr(self, 'filename'):
            return None
        # if not isinstance(self.filename, str):
        #     return None
        # elif self.filename is None:
        #     return "array"
        # if exists and not path.isfile(self.filename):
        #     return None
        for k in self._ids.keys():
            if self._ids[k](self.filename, *args, **kwargs):
                return k
        return None

    def plot(self, *args, **kwargs):
        """
        Generates a 1D plot based on a cross section of the current data.

        Uses parameters from dataproc.plot()
        ---------
        axes: int, plt.Figure, plt.Axes, optional
        title: str, optional
        xlim : tuple, optional
            Section of the x-axis to plot
        ylim : tuple, optional
            Section of the y-axis to plot
        ticks : bool, optional
            Whether to display the ticks
        colorbar: bool, optional
            Wheteher to use a colorbar
        hdu : int, optional
            HDU to plot
        rotate : int, optional
        pos : int, optional
        forcenew : bool, optional
            Whether to create a new plot if no axis has been specified

        Returns
        -------
        array_like, array_like
            A copy of the data used and the 1D array used for the plot

        See Also
        --------
        misc_graph.plot_accross
        """

        return dp.plot_accross(self.reader(), *args, **kwargs)

    def add_sortkey(self, key):
        """
        Sets a header item used for sorting comparisons

        Parameters
        ----------
        key: str
        """
        self.sort_key = key

    def imshowz(self, *args, **kwargs):
        """
        Plots frame after being processed using a zscale algorithm

        See Also
        --------
        misc_graph.imshowz :
            For details on what keyword arguments are available
        """
        hdu = kwargs.pop('hdu', self._hdud)

        return dp.imshowz(self.reader(hdu=hdu), *args, **kwargs)

    def __nonzero__(self):
        return hasattr(self, 'filename') and (self.type is not None)

    @_AstroDecorator.check_filename
    def filter(self, **kwargs):
        """
        Compares if the expected value of the given header item matches
        the value stored in this file header.

        Filtering can be customized by including one of the following options
        after the item name, separated by an underscore.

        - Strings:
            + BEGIN:     True if value starts with the given string
            + END:       True if value ends with the given string
            + ICASE:     Case unsensitive match
            + MATCH:     Case sensitive match

        - Numeric values:
            + LT:        True if current value is lower than the given number
            + GT:        True if current value is greater than the given number
            + EQUAL:     True if both values are the same
        - Other:
            + NOT:       Logical Not

        Its possible to include multiple options in a 1-element dictionary using
           {'cast':[val1,val2,val3]}
        or as different keywords. Both these statements count as a logical "or"
        construction. For "and" build a series of concatenated calls, as in
           .filter(cond1=val1).filter(cond2=val2)

        Parameters
        ----------
        kwargs : Keyword Arguments or unpacked dictionary
            item_name_option : value

        Returns
        -------
        bool

        Notes
        -----
        If a header item has a "_" character on its name, use two underscores
        to represent it.

        Examples
        --------
        NAME_BEGIN_ICASE_NOT = "WASP"   (False if string starts with wasp)
        NAXIS1_GT_EQUAL = 20                  (True if NAXIS1 >= 20)

        """
        filters = self.parse_filter(**kwargs)

        return True in [False
                        if pd.isna(value)
                        else (True in [operation(value_cast(value, r), request_cast(value, r))
                                       for r in request])
                        for value_cast, request_cast, operation, request, filter_keyword in filters
                        for value in [self[filter_keyword]]]


    @_AstroDecorator.check_filename
    def setheader(self, **kwargs):
        """
        Set header values from kwargs. They can be specified as tuple to add
        comments as in pyfits.

        Parameters
        ----------
        **kwargs : Keyword argument or unpacked dict

        Examples
        --------
        setheader(item1 = data1, item2 = data2)
        ``setheader(**{"item1" : data1, "item2" : data2})``

        Returns
        -------
        True if header was edited properly

        Notes
        -----
        Setting a header value to None will remove said item from the header
        """

        tp = self.type
        hdu = kwargs.pop('hduh', self._hduh)
        for k, v in kwargs.items():
            key = f"{hdu}_{k}"
            if v is None and key in self._info:
                self._info[key] = pd.NA
            else:
                self._info[key] = v
        return self._seth[tp](self._info.name, hdu=hdu, **kwargs)

    @_AstroDecorator.check_filename
    def getheaderval(self, *args, **kwargs):
        """
        Get header value for each of the fields specified in args.

        Parameters
        ----------
        args : list, tuple
          One string per argument, or a list as a single argument:
          getheaderval(field1,field2,...) or
          getheaderval([field1,field2,...]).
          In addition to return headers from the file, it can return "basename"
          and "dirname" from the filename itself if available.
        cast : function, optional
          Function to use for casting each element of the result.
        hdu :  int, optional
          HDU to read.

        Returns
        -------
        list
            If multiple values are found
        string
            If only one result was found
        """

        tp = self.type

        cast = kwargs.pop('cast', lambda x: x)
        hdu = kwargs.pop('hdu', self._hduh)

        if len(args) == 1:
            # If first argument is tuple use those values as searches
            if isinstance(args[0], (list, tuple)):
                args = args[0]
            # If its a string, separate by commas
            elif isinstance(args[0], str):
                args = args[0].split(',')

        hdr = self._info
        ret = []
        for k in args:
            k_lc = f"{hdu}_{k.lower().strip()}"
            if k_lc == "basename":
                ret.append(path.basename(self._info.name))
            elif k_lc == "dirname":
                ret.append(path.dirname(self._info.name))
            elif k_lc == "filename":
                ret.append(self._info.name)
            elif k_lc in hdr:
                ret.append(cast(hdr[k_lc]))
            else:
                ret.append(None)

        if len(ret) == 1:
            return ret[0]
        else:
            return ret

    @_AstroDecorator.check_filename
    def reader(self, *args, **kwargs):
        """
        Read astro data and return it calibrated if provided

        TODO: Respond to different exposure times or filters

        Parameters
        ----------
        hdu, hdud: int, optional
            hdu slot to be read
        rawdata : bool, optional
            If True returns data without calibration

        """
        tp = self.type

        hdu = kwargs.pop('hdud', None)
        if hdu is None:
            hdu = kwargs.pop('hdu', self._hdud)
        kwargs.pop('hdu', None)

        if not tp:
            return False

        data = self._reads[tp](self.filename, *args, hdu=hdu, **kwargs)

        if data is None:  # File with no data / corrupt
            raise IOError(f"Empty or corrupt file {self.filename} or HDU {hdu}")

        if 'hdu' in kwargs and kwargs['hdu'] < 0:
            raise ValueError("Invalid HDU specification ({hdu})"
                             "in file {file}?\n available: {hdus}"
                             .format(hdu=hdu,
                                     file=self.filename,
                                     hdus=self.reader(hdu=-1)))
        if 'rawdata' in kwargs and kwargs['rawdata']:
            return data

        # transform into CCDData
        # ccddata = ndd.CCDData(data, unit=self.unit, meta=self.header_cache,
        #                       uncertainty=ndd.StdDevUncertainty(
        #                                   np.sqrt(self.ron*self.ron
        #                                           + data*self.gain)
        #                                           / self.gain))

        if self.has_calib():
            return self.calib.reduce(data,
                                     exptime=self[self.calib.exptime_keyword],
                                     ffilter=self[self.calib.filter_keyword],
                                     header=self._info)  # todo: check header!

        return data

    def has_calib(self):
        """
        Checks if a bias or flat has been associated with this file

        Returns
        -------
        bool
        """
        return self.calib.has_flat or self.calib.has_bias

    @_AstroDecorator.check_filename
    def readheader(self, use_header=None):
        """
        Reads header from the file

        Parameters
        ----------
            use_header:
        """
        if self._info is not None:
            return self._info

        tp = self.type

        if not tp:
            return False

        if use_header is None:
            header = self._geth[self.type](self.filename)
        elif isinstance(use_header, dict):
            header = [use_header]
        else:
            raise ValueError(f"Header argument to AstroFile can only be a dictionary")

        header_dict = {}
        for hdu in range(len(header)):
            header_dict = self.dict_to_header(header[hdu], hdu=hdu, append_to=header_dict)

        self._info = pd.Series(header_dict, name=self.filename)

        return self._info

    @_AstroDecorator.check_filename
    def writer(self, data, *args, **kwargs):
        """
        Writes given data to file
        """
        tp = self.type
        # TODO: Save itself if data exists. Now it only saves explicit
        #       array given by user
        return tp and self._writes[tp](self.filename, data, *args, **kwargs)

    @_AstroDecorator.check_filename
    def basename(self):
        """
        Obtains the file basename

        Returns
        -------
        str
        """
        import os.path as path
        if not hasattr(self, 'filename'):  # TODO ojo aca
            return None
        return path.basename(self.filename)

    @_AstroDecorator.check_filename
    def __getitem__(self, key):
        """
        Read data and return key

        Returns
        -------
        If key is string, it returns either a one element or a list of headers.
        Otherwise, it returns an index to its data content
        """

        if key is None:
            return None
        elif isinstance(key, str):
            return self.getheaderval(key)
        else:
            return self.reader()[key]

    # Object Comparison
    @_AstroDecorator.check_sort_key
    def __lt__(self, other):
        return self.getheaderval(self.sort_key) < \
               other.getheaderval(self.sort_key)

    @_AstroDecorator.check_sort_key
    def __le__(self, other):
        return self.getheaderval(self.sort_key) <= \
               other.getheaderval(self.sort_key)

    @_AstroDecorator.check_sort_key
    def __gt__(self, other):
        return self.getheaderval(self.sort_key) > \
               other.getheaderval(self.sort_key)

    @_AstroDecorator.check_sort_key
    def __eq__(self, other):
        return self.getheaderval(self.sort_key) == \
               other.getheaderval(self.sort_key)

    @_AstroDecorator.check_sort_key
    def __ne__(self, other):
        return self.getheaderval(self.sort_key) != \
               other.getheaderval(self.sort_key)

    # Object Arithmetic
    @_AstroDecorator.numerize_other
    def __add__(self, other):
        return self.reader() + other

    @_AstroDecorator.numerize_other
    def __sub__(self, other):
        return self.reader() - other

    @_AstroDecorator.numerize_other
    def __floordiv__(self, other):
        return self.reader() // other

    @_AstroDecorator.numerize_other
    def __truediv__(self, other):
        return self.reader() / other

    @_AstroDecorator.numerize_other
    def __mul__(self, other):
        return self.reader() * other

    @_AstroDecorator.numerize_other
    def __radd__(self, other):
        return other + self.reader()

    @_AstroDecorator.numerize_other
    def __rsub__(self, other):
        return other - self.reader()

    @_AstroDecorator.numerize_other
    def __rfloordiv__(self, other):
        return other // self.reader()

    @_AstroDecorator.numerize_other
    def __rtruediv__(self, other):
        return other / self.reader()

    @_AstroDecorator.numerize_other
    def __rmul__(self, other):
        return self.reader() * other

    def __len__(self):
        return len(self.reader())

    @property
    def shape(self):
        return self.reader().shape

    def stats(self, *args, **kwargs):
        """
        Calculates statistical data from the file, a request can include header
        keywords to be included in the response.

        Parameters
        ----------
        *args : str
            Statistic to be extracted, Possible values are: min, max, mean,
            mean3sclip, std and median

        **kwargs : str
            Options available: verbose_heading and extra_headers

        Returns
        -------
        list
            List containing the requested data

        """
        verbose_heading = kwargs.pop('verbose_heading', True)
        extra_headers = kwargs.pop('extra_headers', [])
        if kwargs:
            raise SyntaxError(
                "Only the following keyword argument for stats are"
                " permitted 'verbose_heading', 'extra_headers'")

        if not args:
            args = ['min', 'max', 'mean', 'mean3sclip', 'median', 'std']
        if verbose_heading:
            print("Computing the following stats: {} {}"
                  .format(", ".join(args),
                          len(extra_headers)
                          and "\nand showing the following headers: {}"
                              .format(", ".join(extra_headers))
                          or ""))
        ret = []
        data = self.reader()
        for stat in args:
            if stat == 'min':
                ret.append(data.min())
            elif stat == 'max':
                ret.append(data.max())
            elif stat == 'mean':
                ret.append(data.mean())
            elif stat == 'mean3sclip':
                clip = data.copy().astype("float64")
                clip -= data.mean()
                std = data.std()
                ret.append(clip[np.absolute(clip) < 3 * std].mean())
            elif stat == 'std':
                ret.append(data.std())
            elif stat == 'median':
                ret.append(np.median(data))
            else:
                raise ValueError("Unknown stat '{}' was requested for "
                                 "file {}".format(stat, self.filename))
        for h in extra_headers:
            ret.append(self[h])

        return ret

    def jd_from_ut(self, target='jd', source='date-obs'):
        """
        Add jd in header's cache to keyword 'target' using ut on keyword
        'source'

        Parameters
        ----------
        target : str
            Target keyword for JD storage
        source : str or list
            Input value in UT format, if a tuple is given it will join the
            values obtained from both keywords following the UT format
            (day+T+time)

        """
        newhd = {}
        if isinstance(source, list) is True:
            newhd[target] = apt.Time(self[source[0]]+"T"+self[source[1]]).jd
        else:
            newhd[target] = apt.Time(self[source]).jd
        self.setheader(**newhd)

    def merger(self, start=1, end=None):
        """
        Merges multiple ImageHDU objects into a single composite image
        , saves the new ImageHDU into a new hdu slot.

        Parameters
        ----------
        start: int, optional
            HDU slot from which the algorithm starts concatenating images
        end: int, optional
            Last HDU to be merged, if None will continue until the end of the
            HDUList
        """
        try:
            f = pf.open(self.filename, mode='update', memmap=False)
            read_only = False
        except IOError:
            f = pf.open(self.filename)
            read_only = True

        with f as fit:
            # This file already has a composite image
            if fit[-1].header.get('COMP') is not None:
                fit.close()
                return self

            if end is None:
                end = len(fit)

            dataset = []
            for i in range(start, end):
                if isinstance(fit[i], (pf.ImageHDU, pf.PrimaryHDU)):
                    mat = fit[i].data
                    if mat is None:
                        warnings.warn("Warning: {} contains empty data at "
                                      "hdu = {}, skipping hdu"
                                      .format(self.basename, i))
                    else:
                        dataset.append(mat)
                else:
                    raise(TypeError,
                          "Unable to merge hdu of type: " + type(fit[i]))

            comp = np.concatenate(dataset, axis=1)

            composite = pf.ImageHDU(comp, header=fit[0].header)
            composite.header.set('COMP', True)
            if read_only:
                comp_hdu = len(fit)
                self._hdud = comp_hdu
                self._hduh = comp_hdu
                self._info = self.dict_to_header(composite.header, hdu=comp_hdu, append_to=self._info)

                self.type = self.checktype()
                warnings.warn("Merged image stored in hdu 0 and previous info discarded as system was read-only",
                              UserWarning,
                              )

            else:
                fit.append(composite)
                fit.flush()

        return self

    @staticmethod
    def dict_to_header(header_dict, hdu=0, append_to=None, name=None):
        header_dict = {f"{hdu}_{k}": v for k, v in header_dict.items()}
        if append_to is None:
            return pd.Series(header_dict, name=name)
        elif isinstance(append_to, pd.Series):
            append_to.update(header_dict)
        else:
            raise ValueError(f"append_to keyword to .dict_to_header() must be a pandas.Series")

    #################
    #
    # WISHLIST FROM HERE. BASIC IMPLEMENTATION OF METHODS
    # TODO : Improve
    #
    #############################
    def spplot(self,
               axes=None, title=None, xtitle=None, ytitle=None,
               *args, **kwargs):

        """
        Plots spectral data contained on this file
        """
        fig, ax = dp.prep_canvas(axes, title, xtitle, ytitle)

        data = self.reader()
        dim = len(data.shape)

        if dim == 2:
            if data.shape[0] < data.shape[1]:
                wav = data[0, :]
                flx = data[1, :]
            else:
                wav = data[:, 0]
                flx = data[:, 1]
        elif dim == 1:
            raise NotImplementedError(
                "Needs to add reading of wavelength from headers")
        else:
            raise NotImplementedError("Spectra not understood")

        ax.plot(wav, flx, *args, **kwargs)


class AstroCalib(object):
    """
    Object to hold calibration frames.

    Since several AstroFiles might use the same calibration frames, one
    AstroCalib object might be shared by more than one AstroFile. For instance,
    all the files initialized through AstroDir share a single calibration
    object.

    Attributes
    ----------
    has_bias : bool
    has_flat : bool

    Parameters
    ----------
    mbias : dict indexed by exposure time, array or AstroFile
        Master Bias
    mflat : dict indexed by filter name, array or AstroFile
        Master Flat
    exptime_keyword : str, optional
        Header item name containing the exposure time of te frames
    mbias_header : astropy.io.fits.Header
        Header of the master bias
    mflat_header : astropy.io.fits.Header
        Header of the master bias
    filter_keyword : str, optional
    auto_trim : str
        Header field name which is used to cut a portion of the data. This
        field should contain the dimensions of the section to be cut.
        (i.e. TRIMSEC = '[1:1992,1:1708]')
    """

    def __init__(self, mbias=None, mflat=None,
                 exptime_keyword='exptime',
                 mbias_header=None, mflat_header=None,
                 filter_keyword='filter',
                 auto_trim=None):

        # Itss always created false, if the add_*() has something, then its
        # turned true.
        self.has_bias = self.has_flat = False

        self.mbias = {}
        self.mflat = {}
        self.exptime_keyword = exptime_keyword
        self.filter_keyword = filter_keyword
        self.add_bias(mbias, mbias_header=mbias_header)
        self.add_flat(mflat, mflat_header=mflat_header)
        self.auto_trim = auto_trim

    def add_bias(self, mbias, mbias_header=None):
        """
        Add Master Bias to Calib object.

        Parameters
        ----------
        mbias : dict indexed by exposure time, array_like or AstroFile
            Master bias to be included
        mbias_header: astropy.io.fits.Header
            Header to be included

        Raises
        ------
        ValueError
            If the bias type is invalid
        """
        self.mbias_header = mbias_header
        if mbias is None:
            self.mbias[-1] = 0.0
            return
        self.has_bias = True
        if isinstance(mbias, dict):
            for k in mbias.keys():
                self.mbias[k] = mbias[k]
        elif isinstance(mbias,
                        (int, float, np.ndarray)):
            self.mbias[-1] = mbias
        elif isinstance(mbias,
                        dp.AstroFile):
            self.mbias[-1] = mbias.reader()
            self.mbias_header = mbias.readheader()
        else:
            raise ValueError("Master Bias supplied was not recognized.")

    def add_flat(self, mflat, mflat_header=None):
        """
        Add master flat to Calib object

        Parameters
        ----------
        mflat: dict indexed by filter name, array_like, AstroFile
            Master flat to be included
        mflat_header: astropy.io.fits.Header, optional
            Master flat header

        Raises
        ------
        ValueError
            If the bias type is invalid
        """

        self.mflat_header = mflat_header
        if mflat is None:
            self.mflat[''] = 1.0
            return
        self.has_flat = True
        if isinstance(mflat, dict):
            for k in mflat.keys():
                self.mflat[k] = mflat[k]
        elif isinstance(mflat,
                        dp.AstroFile):
            self.mflat[''] = mflat.reader()
            self.mflat_header = mflat.readheader()
        elif isinstance(mflat,
                        (int, float, np.ndarray)):
            self.mflat[''] = mflat
        else:
            raise ValueError("Master Flat supplied was not recognized.")

    def reduce(self, data, exptime=None, ffilter=None, header=None):
        """
        Process given "data" using the bias and flat contained in this instance

        Parameters
        ----------
        data : array_like
            Data to be reduced
        exptime : int, optional
            Exposure time for bias
        ffilter : str, optional
            Filter used by the flat
        header : astropy.io.fits.Header, optional

        Returns
        -------
        array_like
            Reduced data

        """

        if exptime is None or exptime not in self.mbias:
            exptime = -1
        if ffilter is None or ffilter not in self.mflat:
            ffilter = ''

        if exptime not in self.mbias:
            raise ValueError(
                "Requested exposure time ({}) is not available "
                "for mbias, only: {}".format(exptime,
                                             ", "
                                             .join(map(str,
                                                       self.mbias.keys()))))
        if ffilter not in self.mflat:
            raise ValueError(
                "Requested filter ({}) is not available for mflat, only: {}"
                .format(ffilter, ", ".join(self.mflat.keys())))

        flat = self.mflat[ffilter]
        bias = self.mbias[exptime]

        mintrim = np.array([0, data.shape[0], 0, data.shape[1]])
        calib_arrays = [flat, bias, data]

        if self.auto_trim is not None:
            out_data = []

            # TODO: Do not use array size to compare trimsec, but array
            #       section instead.
            for tdata, theader, label in zip(calib_arrays,
                                             [self.mflat_header,
                                              self.mbias_header,
                                              header],
                                             ["master flat",
                                              "master bias",
                                              "data"]):

                if isinstance(tdata, (float, int)):
                    out_data.append(tdata)
                    continue

                if theader is not None and self.auto_trim in theader:

                    trim = np.array(re.search(r'\[(\d+):(\d+),(\d+):(\d+)\]',
                                              theader[self.auto_trim])
                                    .group(1, 2, 3, 4))[np.array([2, 3, 0, 1])]
                    trim = [int(t) for t in trim]
                    # Note the "-1" at the end since fits specifies indices
                    # from 1 and not from 0
                    trim[0] -= 1
                    trim[2] -= 1
                    logging.warning("Adjusting size of {} to [{}:{}, {}:{}] "
                                    " (from [{}, {}])".format(label,
                                                              trim[0], trim[1],
                                                              trim[2], trim[3],
                                                              tdata.shape[0],
                                                              tdata.shape[1]))
                    tdata = tdata[mintrim[0]:mintrim[1], mintrim[2]:mintrim[3]]
                    if len(trim) != 4:
                        logging.warning(
                            "Auto trim field '{}' with invalid format in "
                            "{} ({}). Using full array size."
                            .format(self.auto_trim,
                                    label,
                                    theader[self.auto_trim]))

                out_data.append(tdata)

            data = out_data[0]
            flat = out_data[1]
            bias = out_data[2]

        debias = data - bias
        deflat = debias / flat

        return deflat
