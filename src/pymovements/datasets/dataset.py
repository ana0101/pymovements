# Copyright (c) 2022-2023 The pymovements Project Authors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""This module provides the base dataset class."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl
from tqdm.auto import tqdm

from pymovements.base import Experiment
from pymovements.utils.paths import get_filepaths


class Dataset:
    """Dataset base class."""

    def __init__(
        self,
        root: str | Path,
        experiment: Experiment | None = None,
        filename_regex: str = '.*',
        filename_regex_dtypes: dict[str, type] | None = None,
        custom_read_kwargs: dict[str, Any] | None = None,
    ):
        """Initialize the dataset object.

        Parameters
        ----------
        root : str, Path
            Path to the root directory of the dataset.
        experiment : Experiment
            The experiment definition.
        filename_regex : str
            Regular expression which needs to be matched before trying to load the file. Named
            groups will appear in the `fileinfo` dataframe.
        filename_regex_dtypes : dict[str, type], optional
            If named groups are present in the `filename_regex`, this makes it possible to cast
            specific named groups to a particular datatype.
        custom_read_kwargs : dict[str, Any], optional
            If specified, these keyword arguments will be passed to the file reading function.
        """
        self.fileinfo = None
        self.gaze = None

        self.root = Path(root)

        self.experiment = experiment

        self._filename_regex = filename_regex

        if filename_regex_dtypes is None:
            filename_regex_dtypes = {}
        self._filename_regex_dtypes = filename_regex_dtypes

        if custom_read_kwargs is None:
            custom_read_kwargs = {}
        self._custom_read_kwargs = custom_read_kwargs

    def load(self):
        """Parse file information and load all gaze files.

        The parsed file information is assigned to the `fileinfo` attribute.
        All gaze files will be loaded as dataframes and assigned to the `gaze` attribute.
        """
        self.fileinfo = self.infer_fileinfo()
        self.gaze = self.load_gaze_files()

    def infer_fileinfo(self) -> pl.DataFrame:
        """Infer information from filepaths and filenames.

        Returns
        -------
        pl.DataFrame :
            File information dataframe.

        Raises
        ------
        AttributeError
            If no regular expression for parsing filenames is defined.
        RuntimeError
            If an error occured during matching filenames.
        """
        if self._filename_regex is not None:
            filename_regex = re.compile(self._filename_regex)
        else:
            raise AttributeError('no regular expression for filenames is defined.')

        # Get all filepaths that match regular expression.
        csv_filepaths = get_filepaths(
            path=self.raw_rootpath,
            regex=filename_regex,
        )

        # Parse fileinfo from filenames.
        fileinfo_records: list[dict[str, Any]] = []
        for filepath in csv_filepaths:

            # All csv_filepaths already match the filename_regex.
            match = filename_regex.match(filepath.name)

            # This actually should never happen but mypy will complain otherwise.
            if match is None:
                raise RuntimeError(
                    f'file {filepath} did not match regular expression {self._filename_regex}',
                )

            # We use the groupdict of the match as a base and add the filepath.
            fileinfo_record = match.groupdict()

            for fileinfo_key, fileinfo_dtype in self._filename_regex_dtypes.items():
                fileinfo_record[fileinfo_key] = fileinfo_dtype(fileinfo_record[fileinfo_key])

            fileinfo_record['filepath'] = str(filepath)
            fileinfo_records.append(fileinfo_record)

        # Create dataframe from all fileinfo records.
        fileinfo_df = pl.from_dicts(dicts=fileinfo_records, infer_schema_length=1)
        fileinfo_df = fileinfo_df.sort(by='filepath')

        return fileinfo_df

    def load_gaze_files(self) -> list[pl.DataFrame]:
        """Load all available gaze data files.

        Returns
        -------
        list[pl.DataFrame]
            List of gaze dataframes.

        Raises
        ------
        AttributeError
            If `fileinfo` is None or the `fileinfo` dataframe is empty.
        """
        file_dfs: list[pl.DataFrame] = []

        if self.fileinfo is None:
            raise AttributeError(
                'fileinfo was not loaded yet. please run load() or infer_fileinfo() beforehand',
            )
        if len(self.fileinfo) == 0:
            raise AttributeError('no files present in fileinfo attribute')

        # read and preprocess input files
        for file_id, filepath in enumerate(tqdm(self.fileinfo['filepath'])):
            file_df = pl.read_csv(filepath, **self._custom_read_kwargs)

            for column in self.fileinfo.columns[::-1]:
                if column == 'filepath':
                    continue

                column_value = self.fileinfo.select(column)[file_id][0, 0]
                file_df = file_df.select([
                    pl.lit(column_value).alias(column),
                    pl.all(),
                ])

            file_dfs.append(file_df)

        return file_dfs

    def pix2deg(self, verbose: bool = True) -> None:
        """Compute gaze positions in degrees of visual angle from pixel coordinates.

        This requires an experiment definition and also assumes that the columns 'x_left_pix',
         'y_left_pix', 'x_right_pix' and 'y_right_pix' are available in the gaze dataframe.

        After success, the gaze dataframe is extended by the columns 'x_left_dva', 'y_left_dva',
        'x_right_dva' and, 'y_right_dva'.

        Parameters
        ----------
        verbose : bool
            If True, show progress of computation.

        Raises
        ------
        AttributeError
            If `gaze` is None or there are no gaze dataframes present in the `gaze` attribute, or
            if experiment is None.
        """
        if self.gaze is None:
            raise AttributeError(
                'gaze files were not loaded yet. please run load() or load_gaze_files() beforehand',
            )
        if len(self.gaze) == 0:
            raise AttributeError('no files present in gaze attribute')
        if self.experiment is None:
            raise AttributeError('experiment must be specified for this method.')

        disable_progressbar = not verbose

        for file_id, file_df in enumerate(tqdm(self.gaze, disable=disable_progressbar)):
            pix_position_columns = ['x_left_pix', 'y_left_pix', 'x_right_pix', 'y_right_pix']
            dva_position_columns = ['x_left_dva', 'y_left_dva', 'x_right_dva', 'y_right_dva']

            pixel_positions = file_df.select(pix_position_columns)

            dva_positions = self.experiment.screen.pix2deg(pixel_positions.transpose())

            for dva_column_id, dva_column_name in enumerate(dva_position_columns):
                self.gaze[file_id] = self.gaze[file_id].with_columns(
                    pl.Series(name=dva_column_name, values=dva_positions[:, dva_column_id]),
                )

    def pos2vel(self, method: str = 'smooth', verbose: bool = True, **kwargs) -> None:
        """Compute gaze velocites in dva/s from dva coordinates.

        This requires an experiment definition and also assumes that the columns 'x_left_dva',
         'y_left_dva', 'x_right_dva' and 'y_right_dva' are available in the gaze dataframe.

        After success, the gaze dataframe is extended by the columns 'x_left_vel', 'y_left_vel',
        'x_right_vel' and, 'y_right_vel'.

        Parameters
        ----------
        method : str
            Computation method. See :func:`~transforms.pos2vel()` for details, default: smooth.
        verbose : bool
            If True, show progress of computation.
        **kwargs
            Additional keyword arguments to be passed to the :func:`~transforms.pos2vel()` method.

        Raises
        ------
        AttributeError
            If `gaze` is None or there are no gaze dataframes present in the `gaze` attribute, or
            if experiment is None.
        """
        if self.gaze is None:
            raise AttributeError(
                'gaze files were not loaded yet. please run load() or load_gaze_files() beforehand',
            )
        if len(self.gaze) == 0:
            raise AttributeError('no files present in gaze attribute')
        if self.experiment is None:
            raise AttributeError('experiment must be specified for this method.')

        disable_progressbar = not verbose

        for file_id, file_df in enumerate(tqdm(self.gaze, disable=disable_progressbar)):
            position_columns = ['x_left_dva', 'y_left_dva', 'x_right_dva', 'y_right_dva']
            velocity_columns = ['x_left_vel', 'y_left_vel', 'x_right_vel', 'y_right_vel']

            positions = file_df.select(position_columns)

            velocities = self.experiment.pos2vel(positions.transpose(), method=method, **kwargs)

            for col_id, velocity_column_name in enumerate(velocity_columns):
                self.gaze[file_id] = self.gaze[file_id].with_columns(
                    pl.Series(name=velocity_column_name, values=velocities[:, col_id]),
                )

    @property
    def rootpath(self) -> Path:
        """Get the path to the dataset directory.

        The dataset path points to a directory in the specified root directory which is named the
        same as the respective class.

        Example
        -------
        >>> class CustomDataset(Dataset):
        ...     pass
        >>> dataset = CustomDataset(root='data')
        >>> dataset.rootpath  # doctest: +SKIP
        Path('data/CustomDataset')
        """
        return self.root / self.__class__.__name__

    @property
    def raw_rootpath(self) -> Path:
        """Get the path to the directory of the raw data.

        The raw data directory path points to a directory named `raw` in the dataset `rootpath`.

        Example
        -------
        >>> class CustomDataset(Dataset):
        ...     pass
        >>> dataset = CustomDataset(root='data')
        >>> dataset.raw_rootpath  # doctest: +SKIP
        Path('data/CustomDataset/raw')
        """
        return self.rootpath / 'raw'
