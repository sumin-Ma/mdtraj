##############################################################################
# MDTraj: A Python Library for Loading, Saving, and Manipulating
#         Molecular Dynamics Trajectories.
# Copyright 2012-2013 Stanford University and the Authors
#
# Authors: Robert McGibbon
# Contributors:
#
# MDTraj is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation, either version 2.1
# of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with MDTraj. If not, see <http://www.gnu.org/licenses/>.
##############################################################################


##############################################################################
# Imports
##############################################################################

from __future__ import print_function, division

import os
import itertools

import numpy as np

from mdtraj.utils import ensure_type, cast_indices, in_units_of
from mdtraj.formats.registry import _FormatRegistry
from mdtraj.utils.six import string_types
from mdtraj.utils.six.moves import xrange

__all__ = ['LAMMPSTrajectoryFile', 'load_lammpstrj']


class _EOF(IOError):
    pass


@_FormatRegistry.register_loader('.lammpstrj')
def load_lammpstrj(filename, top=None, stride=None, atom_indices=None,
                   frame=None, unit_set='real'):
    """Load a LAMMPS trajectory file.

    Parameters
    ----------
    filename : str
        String filename of LAMMPS trajectory file.
    top : {str, Trajectory, Topology}
        The lammpstrj format does not contain topology information. Pass in
        either the path to a pdb file, a trajectory, or a topology to supply
        this information.
    stride : int, default=None
        Only read every stride-th frame
    atom_indices : array_like, optional
        If not none, then read only a subset of the atoms coordinates from the
        file.
    frame : int, optional
        Use this option to load only a single frame from a trajectory on disk.
        If frame is None, the default, the entire trajectory will be loaded.
        If supplied, ``stride`` will be ignored.
    unit_set : str, optional
        The LAMMPS unit set that the simulation was performed in. See
        http://lammps.sandia.gov/doc/units.html for options. Currently supported
        unit sets: 'real'.

    Returns
    -------
    trajectory : md.Trajectory
        The resulting trajectory, as an md.Trajectory object.

    See Also
    --------
    mdtraj.LAMMPSTrajectoryFile :  Low level interface to lammpstrj files
    """
    from mdtraj.core.trajectory import _parse_topology, Trajectory

    # we make it not required in the signature, but required here. although this
    # is a little weird, its good because this function is usually called by a
    # dispatch from load(), where top comes from **kwargs. So if its not supplied
    # we want to give the user an informative error message
    if top is None:
        raise ValueError('"top" argument is required for load_lammpstrj')

    if not isinstance(filename, string_types):
        raise TypeError('filename must be of type string for load_lammpstrj. '
                        'you supplied %s'.format(type(filename)))

    topology = _parse_topology(top)
    atom_indices = cast_indices(atom_indices)
    if atom_indices is not None:
        topology = topology.subset(atom_indices)

    with LAMMPSTrajectoryFile(filename) as f:
        # TODO: Support other unit sets.
        if unit_set == 'real':
            f.distance_unit == 'angstroms'
        else:
            raise ValueError('Unsupported unit set specified: {}.'.format(unit_set))
        if frame is not None:
            f.seek(frame)
            xyz, cell_lengths = f.read(n_frames=1, atom_indices=atom_indices)
        else:
            xyz, cell_lengths = f.read(stride=stride, atom_indices=atom_indices)

        in_units_of(xyz, f.distance_unit, Trajectory._distance_unit, inplace=True)
        in_units_of(cell_lengths, f.distance_unit, Trajectory._distance_unit, inplace=True)
        # TODO: Don't assume that its a rectilinear box.
        cell_angles = 90.0 * np.ones_like(cell_lengths)

    time = np.arange(len(xyz))
    if frame is not None:
        time += frame
    elif stride is not None:
        time *= stride

    t = Trajectory(xyz=xyz, topology=topology, time=time)
    t.unitcell_lengths = cell_lengths
    t.unitcell_angles = cell_angles
    return t


@_FormatRegistry.register_fileobject('.lammpstrj')
class LAMMPSTrajectoryFile(object):
    """Interface for reading and writing to a LAMMPS lammpstrj files.
    This is a file-like object, that both reading or writing depending
    on the `mode` flag. It implements the context manager protocol,
    so you can also use it with the python 'with' statement.

    Parameters
    ----------
    filename : str
        The filename to open. A path to a file on disk.
    mode : {'r', 'w'}
        The mode in which to open the file, either 'r' for read or 'w' for
        write.
    force_overwrite : bool
        If opened in write mode, and a file by the name of `filename` already
        exists on disk, should we overwrite it?
    """

    distance_unit = 'angstroms'

    def __init__(self, filename, mode='r', force_overwrite=True):
        """Open a LAMMPS lammpstrj file for reading/writing.
        """
        self._is_open = False
        self._filename = filename
        self._mode = mode
        self._frame_index = 0
        # track which line we're on. this is not essential, but its useful
        # when reporting errors to the user to say what line it occured on.
        self._line_counter = 0

        if mode == 'r':
            if not os.path.exists(filename):
                raise IOError("The file '%s' doesn't exist" % filename)
            self._fh = open(filename, 'r')
            self._is_open = True
        elif mode == 'w':
            if os.path.exists(filename) and not force_overwrite:
                raise IOError("The file '%s' already exists" % filename)
            self._fh = open(filename, 'w')
            self._is_open = True
        else:
            raise ValueError('mode must be one of "r" or "w". '
                             'you supplied "{}"'.format(mode))

    def close(self):
        """Close the lammpstrj file. """
        if self._is_open:
            self._fh.close()
            self._is_open = False

    def __del__(self):
        self.close()

    def __enter__(self):
        """Support the context manager protocol. """
        return self

    def __exit__(self, *exc_info):
        """Support the context manager protocol. """
        self.close()

    def read(self, n_frames=None, stride=None, atom_indices=None):
        """Read data from a lammpstrj file

        Parameters
        ----------
        n_frames : int, None
            The number of frames you would like to read from the file.
            If None, all of the remaining frames will be loaded.
        stride : np.ndarray, optional
            Read only every stride-th frame.
        atom_indices : array_like, optional
            If not none, then read only a subset of the atoms coordinates
            from the file.

        Returns
        -------
        xyz : np.ndarray, shape=(n_frames, n_atoms, 3), dtype=np.float32
        cell_lengths : {np.ndarray, None}
            If the file contains unitcell lengths, they will be returned as an
            array of shape=(n_frames, 3). Otherwise, unitcell_angles will be
            None.
        """
        if not self._mode == 'r':
            raise ValueError('read() is only available when file is opened '
                             'in mode="r"')

        if n_frames is None:
            frame_counter = itertools.count()
        else:
            frame_counter = xrange(n_frames)

        if stride is None:
            stride = 1

        coords, boxes = [], []
        for _ in frame_counter:
            try:
                coord, box = self._read()
                if atom_indices is not None:
                    coord = coord[atom_indices, :]
            except _EOF:
                break

            coords.append(coord)
            boxes.append(box)

            for j in range(stride - 1):
                # throw away these frames
                try:
                    self._read()
                except _EOF:
                    break

        coords = np.array(coords)
        #coords = in_units_of(coords, self.distance_unit, 'nanometers')
        boxes = np.array(boxes, dtype=np.float32)
        #boxes = in_units_of(boxes, self.distance_unit, 'nanometers')
        return coords, boxes

    def _read(self):
        """Read a single frame. """
        box = np.empty(shape=(3, 2))

        # --- begin header ---
        self._fh.readline()  # ITEM: TIMESTEP
        step = self._fh.readline()  # timestep
        if step == '':
            raise _EOF()
        self._fh.readline()  # ITEM: NUMBER OF ATOMS
        self._n_atoms = int(self._fh.readline())  # num atoms
        self._fh.readline()  # ITEM: BOX BOUNDS xx xx xx
        box[0] = self._fh.readline().split()  # x-dim of box
        box[1] = self._fh.readline().split()  # y-dim of box
        box[2] = self._fh.readline().split()  # z-dim of box
        box = np.diff(box, axis=1).reshape(1, 3)[0]  # box lengths
        self._fh.readline()  # ITEM: ATOMS ...
        self._line_counter += 9
        # --- end header ---

        xyz = np.empty(shape=(self._n_atoms, 3))
        types = np.empty(shape=(self._n_atoms), dtype='int')

        # --- begin body ---
        for _ in xrange(self._n_atoms):
            line = self._fh.readline()
            if line == '':
                raise _EOF()
            temp = line.split()
            try:
                atom_index = int(temp[0])
                types[atom_index - 1] = int(temp[1])
                xyz[atom_index - 1] = [float(x) for x in temp[2:5]]
            except Exception:
                raise IOError('lammpstrj parse error on line {:d} of "{:s}". '
                              'This file does not appear to be a valid '
                              'lammpstrj file.'.format(
                        self._line_counter,  self._filename))
            self._line_counter += 1
        # --- end body ---

        self._frame_index += 1
        return xyz, box

    def write(self, xyz, cell_lengths, types=None, unit_set='real'):
        """Write one or more frames of data to a lammpstrj file

        Parameters
        ----------
        xyz : np.ndarray, shape=(n_frames, n_atoms, 3)
            The cartesian coordinates of the atoms to write.
        cell_lengths : np.ndarray, shape=(n_frames, 3), dtype=float32
            The length of the periodic box in each frame, in each direction,
            `a`, `b`, `c`.
        types : np.ndarray, shape(3, ), dtype=int
            The numeric type of each particle.
        unit_set : str, optional
            The LAMMPS unit set that the simulation was performed in. See
            http://lammps.sandia.gov/doc/units.html for options. Currently supported
            unit sets: 'real'.
        """
        if not self._mode == 'w':
            raise ValueError('write() is only available when file is opened '
                             'in mode="w"')


        xyz = ensure_type(xyz, np.float32, 3, 'xyz', can_be_none=False,
                shape=(None, None, 3), warn_on_cast=False,
                add_newaxis_on_deficient_ndim=True)
        cell_lengths = ensure_type(cell_lengths, np.float32, 2, 'cell_lengths',
                can_be_none=False, shape=(len(xyz), 3), warn_on_cast=False,
                add_newaxis_on_deficient_ndim=True)
        if not types:
            # Make all particles the same type.
            types = np.ones(shape=(xyz.shape[1]))
        types = ensure_type(types, np.int, 1, 'types', can_be_none=True,
                shape=(xyz.shape[1], ), warn_on_cast=False,
                add_newaxis_on_deficient_ndim=False)

        # TODO: Support other unit sets.
        if unit_set == 'real':
            self.distance_unit == 'angstroms'
        else:
            raise ValueError('Unsupported unit set specified: {}.'.format(unit_set))
        xyz = in_units_of(xyz, 'nanometers', self.distance_unit)
        cell_lengths= in_units_of(cell_lengths, 'nanometers', self.distance_unit)

        for i in range(xyz.shape[0]):
            # --- begin header ---
            self._fh.write('ITEM: TIMESTEP\n')
            self._fh.write('{}\n'.format(i))  # TODO: Write actual time if known.
            self._fh.write('ITEM: NUMBER OF ATOMS\n')
            self._fh.write('{}\n'.format(xyz.shape[1]))
            self._fh.write('ITEM: BOX BOUNDS pp pp pp\n')
            mins = xyz[0].min(axis=1)
            self._fh.write('{} {}\n'.format(mins[0], mins[0] + cell_lengths[0][0]))
            self._fh.write('{} {}\n'.format(mins[1], mins[1] + cell_lengths[0][1]))
            self._fh.write('{} {}\n'.format(mins[2], mins[2] + cell_lengths[0][2]))
            # --- end header ---

            # --- begin body ---
            self._fh.write('ITEM: ATOMS id type xu yu zu\n')
            for j, coord in enumerate(xyz[i]):
                self._fh.write('{:d} {:d} {:8.3f} {:8.3f} {:8.3f}\n'.format(
                    j+1, types[j], coord[0], coord[1], coord[2]))
            # --- end body ---

    def seek(self, offset, whence=0):
        """Move to a new file position

        Parameters
        ----------
        offset : int
            A number of frames.
        whence : {0, 1, 2}
            0: offset from start of file, offset should be >=0.
            1: move relative to the current position, positive or negative
            2: move relative to the end of file, offset should be <= 0.
            Seeking beyond the end of a file is not supported
        """
        if self._mode == 'r':
            advance, absolute = None, None
            if whence == 0 and offset >= 0:
                if offset >= self._frame_index:
                    advance = offset - self._frame_index
                else:
                    absolute = offset
            elif whence == 1 and offset >= 0:
                advance = offset
            elif whence == 1 and offset < 0:
                absolute = offset + self._frame_index
            elif whence == 2 and offset <= 0:
                raise NotImplementedError('offsets from the end are not supported yet')
            else:
                raise IOError('Invalid argument')

            if advance is not None:
                for i in range(advance):
                    self._read()  # advance and throw away these frames
            elif absolute is not None:
                self._fh.close()
                self._fh = open(self._filename, 'r')
                self._frame_index = 0
                self._line_counter = 0
                for i in range(absolute):
                    self._read()
            else:
                raise RuntimeError()

        else:
            raise NotImplementedError('offsets in write mode are not supported yet')

    def tell(self):
        """Current file position

        Returns
        -------
        offset : int
            The current frame in the file.
        """
        return int(self._frame_index)

    def __len__(self):
        "Number of frames in the file"
        raise NotImplementedError()


