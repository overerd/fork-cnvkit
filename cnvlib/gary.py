"""Definitions for a generic array of genomic positions."""
from __future__ import print_function, absolute_import

import sys

import numpy as np
import pandas as pd

from . import core, ngfrills


def uniq(arr):
    """Because I don't know how to do this with Pandas yet."""
    # XXX see: pd.Categorical
    # return pd.Categorical(arr, ordered=True)
    prev = None
    for elem in arr:
        if elem != prev:
            yield elem
            prev = elem


# NB: Start by implementing all CNVkit features here, then split later
class GenomicArray(object):
    """An array of genomic intervals.

    Can represent most BED-like tabular formats with arbitrary additional
    columns: SEG, interval list, ...

    Required columns: chromosome, start
    """
    _required_columns = ("chromosome", "start", "end")

    def __init__(self, data_table, meta_dict=None):
        if not all(c in data_table.columns for c in
                   self._required_columns):
            raise ValueError("data table must have at least columns "
                             + repr(self._required_columns))
        self.data = data_table
        self.meta = (dict(meta_dict)
                     if meta_dict is not None and len(meta_dict)
                     else {})

    @staticmethod
    def row2label(row):
        return "{}:{}-{}".format(row['chromosome'], row['start'], row['end'])

    @classmethod
    def from_columns(cls, columns, meta_dict=None):
        """Create a new instance from column arrays, given by name."""
        # TODO - ensure columns are sorted properly
        table = pd.DataFrame.from_dict(columns)
        return cls(table, meta_dict)

    @classmethod
    def from_rows(cls, rows, columns=None, meta_dict=None):
        """Create a new instance from a list of rows, as tuples or arrays."""
        if columns is None:
            columns = cls._required_columns
        table = pd.DataFrame.from_records(rows, columns=columns)
        return cls(table, meta_dict)

    def as_columns(self, **columns):
        """Extract a subset of columns, reusing this instance's metadata."""
        return self.__class__.from_columns(columns, self.meta)
        # return self.__class__(self.data.loc[:, columns], self.meta.copy())

    def as_dataframe(self, dframe):
        return self.__class__(dframe.reset_index(drop=True), self.meta.copy())

    # def as_index(self, index):
    #     """Subset with fancy/boolean indexing; reuse this instance's metadata."""
    #     if isinstance(index, (int, slice)):
    #         return self.__class__(self.data.iloc[index], self.meta.copy())
    #     else:
    #         return self.__class__(self.data[index], self.meta.copy())

    def as_rows(self, rows):
        """Extract rows by indices, reusing this instance's metadata."""
        return self.from_rows(rows,
                              columns=self.data.columns,
                              meta_dict=self.meta)

    # Container behaviour

    def __eq__(self, other):
        return (isinstance(other, self.__class__) and
                self.data.equals(other.data))

    def __len__(self):
        return len(self.data)

    def __contains__(self, key):
        return key in self.data.columns

    def __getitem__(self, index):
        """Access a portion of the data.

        Cases:

        - single integer: a row, as pd.Series
        - string row name: a column, as pd.Series
        - a boolean array: masked rows, as_dataframe
        - tuple of integers: selected rows, as_dataframe
        """
        if isinstance(index, int):
            # A single row
            return self.data.iloc[index]
            # return self.as_dataframe(self.data.iloc[index:index+1])
        elif isinstance(index, basestring):
            # A column, by name
            return self.data[index]
        elif (isinstance(index, tuple) and
              len(index) == 2 and
              index[1] in self.data.columns):
            # Row index, column index -> cell value
            return self.data.loc[index]
        elif isinstance(index, slice):
            # return self.as_dataframe(self.data.take(index))
            return self.as_dataframe(self.data[index])
        else:
            # Iterable -- selected row indices or boolean array, probably
            try:
                if isinstance(index, type(None)) or len(index) == 0:
                    empty = pd.DataFrame(columns=self.data.columns)
                    return self.as_dataframe(empty)
            except TypeError:
                raise TypeError("object of type %r " % type(index) +
                                "cannot be used as an index into a " +
                                self.__class__.__name__)
            return self.as_dataframe(self.data[index])
            # return self.as_dataframe(self.data.take(index))

    def __setitem__(self, index, value):
        """Assign to a portion of the data.
        """
        # self.data[index] = value
        if isinstance(index, int):
            self.data.iloc[index] = value
        elif isinstance(index, basestring):
            self.data[index] = value
        elif (isinstance(index, tuple) and
              len(index) == 2 and
              index[1] in self.data.columns):
            self.data.loc[index] = value
        else:
            assert isinstance(index, slice) or len(index) > 0
            self.data[index] = value

    def __delitem__(self, index):
        return NotImplemented

    def __iter__(self):
        # return iter(self.data)
        return (row for idx, row in self.data.iterrows())

    __next__ = next
    # def __next__(self):
    #     return next(iter(self))

    @property
    def chromosome(self):
        return self.data['chromosome']

    @property
    def start(self):
        return self.data['start']

    @property
    def end(self):
        return self.data['end']

    @property
    def sample_id(self):
        return self.meta.get('sample_id')

    # Traversal

    # XXX troubled
    # or: by_coords, by_ranges
    def by_bin(self, bins, mode='trim'):
        """Group rows by another GenomicArray; trim row start/end to bin edges.

        Returns an iterable of (bin, GenomicArray of overlapping rows))

        modes are:  exclude (drop), trim, include (keep)
            (when coordinates are on a boundary, what happens to the overlapped
            bins? drop, trim to size, include whole)

        default = 'trim': If a probe overlaps with a bin boundary, the probe
        start or end position is replaced with the bin boundary position. Probes
        outside any segments are skipped. This is appropriate for most other
        comparisons between GenomicArray objects.
        """
        # ENH: groupby chromosome?
        for chrom, bin_rows in bins.by_chromosome():
            try:
                cn_rows = self[self.chromosome == chrom]
            except KeyError:
                continue
            # Traverse rows and bins together, matching up start/end points
            for bin_row in bin_rows:
                # ENH: searchsorted w/ start/end arrays?
                binned_rows = cn_rows.in_range(chrom, bin_row['start'],
                                               bin_row['end'],
                                               trim=(mode=='trim'))
                yield bin_row, self.as_rows(binned_rows)

    def by_chromosome(self):
        """Iterate over bins grouped by chromosome name."""
        for chrom in uniq(self.chromosome):
            yield chrom, self[self.chromosome == chrom]

    def coords(self, also=()):
        """Get plain coordinates of each bin: chromosome, start, end.

        With `also`, also include those columns.

        Example:

        >>> probes.coords(also=["name", "strand"])
        """
        cols = list(self._required_columns)
        if also:
            cols.extend(also)
        return self.data.loc[:, cols]

    def labels(self):
        return self.data.apply(self.row2label, axis=1)

    # TODO replace trim=bool w/ mode=trim/drop/keep
    def in_range(self, chrom, start=0, end=None, trim=False):
        """Get the GenomicArray portion within the given genomic range.

        If trim=True, include bins straddling the range boundaries, and trim
        the bins endpoints to the boundaries.
        """
        try:
            table = self.data[self.data['chromosome'] == chrom]
        except KeyError:
            raise KeyError("Chromosome %s is not in this probe set" % chrom)
        if start:
            if trim:
                # Include all rows overlapping the start point
                table = table[table.end.searchsorted(start, 'right'):]
                # Update 5' endpoints to the boundary
                table.start[table.start < start] = start
            else:
                # Only rows entirely after the start point
                table = table[table.start.searchsorted(start):]
        if end:
            if trim:
                table = table[:table.start.searchsorted(end)]
                # Update 3' endpoints to the boundary
                table.end[table.end > end] = end
            else:
                table = table[:table.end.searchsorted(end, 'right')]
        return self.as_dataframe(table)

    def in_ranges(self, chrom, starts=None, ends=None, trim=False):
        """Get the GenomicArray portion within the given genomic range.
        """
        assert isinstance(chrom, basestring)  # ENH: take array?
        try:
            table = self.data[self.data['chromosome'] == chrom]
        except KeyError:
            raise KeyError("Chromosome %s is not in this probe set" % chrom)
        if starts is None and ends is None:
            return self.as_dataframe(table)
        # ENH: Take a series of slices...
        # XXX Slow path:
        if starts is None:
            starts = np.zeros(len(ends), dtype=np.int_)
        subtables = [self.in_range(chrom, start, end, trim).data
                     for start, end in zip(starts, ends)]
        table = pd.concat(subtables)
        return self.as_dataframe(table)

    # Modification

    def add_array(self, other):
        """Combine this array's data with another GenomicArray (in-place).

        Any optional columns must match between both arrays.
        """
        if not isinstance(other, self.__class__):
            raise ValueError("Argument (type %s) is not a %s instance"
                             % (type(other), self.__class__))
        if len(other.data):
            self.data = pd.concat([self.data, other.data])
        self.sort()

    def copy(self):
        """Create an independent copy of this object."""
        return self.as_dataframe(self.data.copy())

    def add_columns(self, **columns):
        """Create a new CNA, adding the specified extra columns to this CNA."""
        # return self.as_dataframe(self.data.assign(**columns))
        result = self.copy()
        for key, values in columns.iteritems():
            result[key] = values
        return result

    def keep_columns(self, columns):
        """Extract a subset of columns, reusing this instance's metadata."""
        return self.__class__(self.data.loc[:, columns], self.meta.copy())

    def drop_extra_columns(self):
        """Remove any optional columns from this GenomicArray.

        Returns a new copy with only the core columns retained:
            log2 value, chromosome, start, end, bin name.
        """
        table = self.data.loc[:, self._required_columns]
        return self.as_dataframe(table)

    def select(self, selector=None, **kwargs):
        """Take a subset of rows where the given condition is true.

        Arguments can be a function (lambda expression) returning a bool, which
        will be used to select True rows, and/or keyword arguments like
        gene="Background" or chromosome="chr7", which will select rows where the
        keyed field equals the specified value.
        """
        table = self.data
        if selector is not None:
            table = table[table.apply(selector, axis=1)]
        for key, val in kwargs.items():
            assert key in self
            table = table[table[key] == val]
        return self.as_dataframe(table)

    def shuffle(self):
        """Randomize the order of bins in this array (in-place)."""
        np.random.seed(0xA5EED)  # For reproducible results
        order = np.arange(len(self.data))
        np.random.shuffle(order)
        self.data = self.data.iloc[order]
        return order

    def sort(self):
        """Sort this array's bins in-place, with smart chromosome ordering."""
        sort_keys = self.chromosome.apply(core.sorter_chrom)
        table = (self.data.assign(SORT_KEY=sort_keys)
                 .sort_index(by=['SORT_KEY', 'start']))
        del table['SORT_KEY']
        self.data = table.reset_index(drop=True)

    # I/O

    @classmethod
    def read(cls, infile, sample_id=None):
        if sample_id is None:
            if isinstance(infile, basestring):
                sample_id = core.fbase(infile)
            else:
                sample_id = '<unknown>'
        # Create a multi-index of genomic coordinates (like GRanges)
        table = pd.read_table(infile, na_filter=False,
                              # index_col=['chromosome', 'start']
        )
        # OR: Replace chromosome names with integers
        # table = pd.read_table(infile, na_filter=False)
        # chrom_names = uniq(table['chromosome'])
        # chrom_ids = np.arange(len(chrom_names))
        # chrom_id_col = np.zeros(len(table), dtype=np.int_)
        # for cn, ci in zip(chrom_names, chrom_ids):
        #     chrom_id_col[table['chromosome'] == cn] = ci
        # table['chromosome'] = chrom_id_col
        # table.set_index(['chromosome', 'start'], inplace=True)
        return cls(table, {"sample_id": sample_id})

    def write(self, outfile=sys.stdout):
        """Write coverage data to a file or handle in tabular format.

        This is similar to BED or BedGraph format, but with extra columns.

        To combine multiple samples in one file and/or convert to another
        format, see the 'export' subcommand.
        """
        with ngfrills.safe_write(outfile) as handle:
            self.data.to_csv(handle, index=False, sep='\t', float_format='%.6g')

