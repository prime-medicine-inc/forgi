from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

from builtins import range

import warnings
import unittest
import sys
import itertools as it
import copy
import time
import math
import logging
import os.path
import os
import shutil
import contextlib

try:
    from unittest.mock import patch
except ImportError:
    from mock import patch

import numpy as np
import numpy.testing as nptest

import forgi.threedee.model.coarse_grain as ftmc
import forgi.graph.bulge_graph as fgb
import forgi.threedee.model.similarity as ftme
import forgi.threedee.utilities.graph_pdb as ftug
import forgi.threedee.utilities.vector as ftuv
import forgi.utilities.debug as fud
from forgi.utilities.stuff import make_temp_directory
from ...graph import bulge_graph_test as tfgb

log = logging.getLogger(__name__)


@contextlib.contextmanager
def ignore_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield None

def cg_from_sg(cg, sg):
    '''
    Create a coarse-grain structure from a subgraph.

    @param cg: The original structure
    @param sg: The list of elements that are in the subgraph
    '''
    new_cg = ftmc.cg_from_sg(cg, sg)
    return new_cg

    for d in sg:
        new_cg.defines[d] = cg.defines[d]

        if d in cg.coords.keys():
            new_cg.coords[d] = cg.coords[d]
        if d in cg.twists.keys():
            new_cg.twists[d] = cg.twists[d]
        if d in cg.longrange.keys():
            new_cg.longrange[d] = cg.longrange[d]

        for x in cg.edges[d]:
            if x in new_cg.defines.keys():
                new_cg.edges[d].add(x)
                new_cg.edges[x].add(d)

    return new_cg


def mock_run_mc_annotate(original_function):
    """
    Caching of MC-Annotate output for speedup
    """
    def mocked_run_mc_annotate(filename, subprocess_kwargs):
        new_fn = os.path.split(filename)[1]
        new_fn += ".mcAnnotate.out"
        try:
            with open(os.path.join("test", "forgi", "threedee", "data", new_fn)) as f:
                lines = f.readlines()
            log.error("Using cached MC-Annotate output")
        except IOError:  # on py3 this is an alias of oserror
            lines = original_function(filename, subprocess_kwargs)
            with open(os.path.join("test", "forgi", "threedee", "data", new_fn), "w") as f:
                print("\n".join(lines), file=f)
        log.info("Returning lines: {}".format(lines))
        return lines
    return mocked_run_mc_annotate


def mocked_read_config():
    """
    Require MC-Annotate for consistency. If not installed, tests should be skipped.
    """
    if not ftmc.which("MC-Annotate"):
        raise unittest.SkipTest("This Test requires MC-Annotate for consistency.")
    else:
        return {"PDB_ANNOTATION_TOOL": "MC-Annotate"}


@patch('forgi.config.read_config', mocked_read_config)
@patch('forgi.threedee.model.coarse_grain._run_mc_annotate',
       mock_run_mc_annotate(ftmc._run_mc_annotate))
class CoarseGrainIoTest(tfgb.GraphVerification):
    def check_cg_integrity(self, cg):
        self.assertGreater(len(list(cg.stem_iterator())), 0)
        for s in cg.stem_iterator():
            edges = list(cg.edges[s])
            if len(edges) < 2:
                continue

            multiloops = False
            for e in edges:
                if e[0] != 'i':
                    multiloops = True

            if multiloops:
                continue

            self.assertFalse(np.allclose(cg.coords[edges[0]][0],
                                         cg.coords[edges[1]][0]))
            self.assertFalse(np.allclose(cg.coords[edges[0]][0],
                                         cg.coords[edges[1]][1]))
            self.assertFalse(np.allclose(cg.coords[edges[0]][1],
                                         cg.coords[edges[1]][0]))
            self.assertFalse(np.allclose(cg.coords[edges[0]][1],
                                         cg.coords[edges[1]][1]))

    def test_dssr_backslash_in_filename(self):
        """
        DSSR puts the input filename in the JSON, which makes the JSON invalid,
        if a backslash is in it. We patch the DSSR JSON before parsing.
        """
        with make_temp_directory() as d:
            # On Windows, bla is a directory, and the backslash is
            # part of the path,
            # on decent operating systems,
            # the backslash is part of the filename.
            filename=os.path.join(d, "bla\\something.pdb")
            dir, rest = os.path.split(filename)
            # On Windows, make the directory bla, on Linux do nothing
            try:
                os.makedirs(dir)
            except OSError:
                # Directory exists
                pass
            shutil.copy('test/forgi/threedee/data/1y26.pdb', filename)
            try:
                # Make sure we do not raise any error.
                cg, = ftmc.CoarseGrainRNA.from_pdb(filename,
                                               annotation_tool="DSSR")
            except ftmc.AnnotationToolNotInstalled:
                self.skipTest("This Test requires DSSR")
        self.check_graph_integrity(cg)
        self.assertGreater(len(cg.defines), 2)

    def test_from_mmcif(self):
        import Bio.PDB as bpdb

        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1Y26.cif')
        cg2, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/1y26.pdb')

        self.assertEqual(cg.defines, cg2.defines)
        self.assertGreater(len(cg.defines), 3)
        for d in cg.defines:
            nptest.assert_almost_equal(cg.coords[d], cg2.coords[d])

    def test_from_mmcif_missing_residues(self):
        import Bio.PDB as bpdb

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/2x1f.cif', load_chains="B")
        cg2, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/2X1F.pdb', load_chains="B")
        log.error(cg.seq._missing_nts)
        self.assertEqual(len(cg.seq._missing_nts), 3)
        self.assertEqual(len(cg2.seq._missing_nts), 3)
        self.assertEqual(cg.seq, cg2.seq)

    def test_from_pdb(self):
        import time
        now = time.time()
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/4GV9.pdb', load_chains='E')
        log.error(time.time() - now)
        now = time.time()
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/RS_363_S_5.pdb')
        log.error(time.time() - now)
        now = time.time()
        self.check_cg_integrity(cg)
        log.error(time.time() - now)
        now = time.time()

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/RS_118_S_0.pdb')
        log.error(time.time() - now)
        now = time.time()

        self.check_cg_integrity(cg)
        log.error(time.time() - now)
        now = time.time()

        self.assertTrue(len(cg.defines) > 1)

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/ideal_1_4_5_8.pdb')
        self.check_cg_integrity(cg)
        log.error(time.time() - now)
        now = time.time()

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/ideal_1_4_5_8.pdb')
        log.error(time.time() - now)
        now = time.time()

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/1y26_missing.pdb')
        self.check_cg_integrity(cg)
        log.error(time.time() - now)
        now = time.time()

        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1y26_two_chains.pdb',
                                           load_chains='Y')
        self.assertEqual(len(cg.defines), 1)
        self.assertIn("f0", cg.defines)
        self.assertEqual(cg.seq, "U")
        log.error(time.time() - now)
        now = time.time()

        # commented out for 3 ec speedup
        # cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1X8W.pdb',
        #                    load_chains='A')
        # self.check_cg_integrity(cg)
        #log.error (time.time()-now); now=time.time()

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/1FJG_reduced.pdb')
        self.check_cg_integrity(cg)
        log.error(time.time() - now)
        now = time.time()

        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1y26.pdb')
        log.error(time.time() - now)
        now = time.time()

    def test_file_with_numeric_chain_id(self):
        # Numeric chain ids
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/3J7A_part.pdb', load_chains=["7"])
        self.check_cg_integrity(cg)
        self.assertEqual(cg.seq._seqids[0].chain, '7')

    def test_from_pdb_cofold(self):
        # 1FUF triggers the if fromA.chain != fromB.chain clause in _are_adjacent_basepairs
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1FUF.pdb',
                                           dissolve_length_one_stems=True)
        self.check_cg_integrity(cg)

    def verify_multiple_chains(self, cg, single_chain_cgs):
        log.warning("Backbone in {} breaks after {}".format(cg.name, cg.backbone_breaks_after))
        self.assertEqual(len(cg.backbone_breaks_after),
                         len(single_chain_cgs) - 1)

        self.assertEqual(cg.seq_length, sum(
            x.seq_length for x in single_chain_cgs))
        # There might be stems spanning multiple chains.
        self.assertGreaterEqual(len([s for s in cg.defines if s[0] == "s"]), len(
            [s for c in single_chain_cgs for s in c.defines if s[0] == "s"]))
        self.assertEqual(cg.seq, "&".join(str(x.seq)
                                          for x in single_chain_cgs))

    def test_from_pdb_f_in_second_chain(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/4GV9.pdb', load_chains=None)
        self.assertEqual(set(cg.defines.keys()), set(["t0", "s0", "f0"]))

    def test_from_pdb_multiple(self):
        cgE, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/4GV9.pdb', load_chains='E')
        cgF, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/4GV9.pdb', load_chains='F')
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/4GV9.pdb', load_chains=None)
        self.assertEqual(set(cg.chains.keys()), set(["E", "F"]))
        self.assertEqual(len(cg.backbone_breaks_after), 1)
        bp = cg.backbone_breaks_after[0]
        self.assertEqual(bp, 3)
        self.assertEqual(cg.seq[:bp], cgE.seq)
        self.assertEqual(cg.seq[1:bp], cgE.seq)
        self.assertEqual(cg.seq[bp + 1:], cgF.seq)
        self.verify_multiple_chains(cg, [cgE, cgF])

        cgA, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/3CQS.pdb', load_chains='A')
        cgB, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/3CQS.pdb', load_chains='B')
        cgC, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/3CQS.pdb', load_chains='C')
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/3CQS.pdb',  load_chains=None)
        log.warning("cg now has {} cutpoints: {}".format(len(cg.seq._breaks_after), cg.backbone_breaks_after))
        self.verify_multiple_chains(cg, [cgA, cgB, cgC])

    def test_multiple_chain_to_cg(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/4GV9.pdb', load_chains=None)
        log.debug("======= FIRST IS LOADED =========")
        cg_str = cg.to_cg_string()
        log.debug("\n" + cg_str)
        print(cg_str)
        cg2 = ftmc.CoarseGrainRNA.from_bg_string(cg_str)
        self.assertEqual(cg.defines, cg2.defines)
        self.assertLess(ftme.cg_rmsd(cg, cg2), 10**-6)
        self.assertEqual(cg.backbone_breaks_after, cg2.backbone_breaks_after)

        cg, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/3CQS.pdb', load_chains=None)
        cg.log(logging.WARNING)
        cg_str = cg.to_cg_string()
        cg2 = ftmc.CoarseGrainRNA.from_bg_string(cg_str)

        self.assertEqual(cg.defines, cg2.defines)
        # This only looks at stems
        self.assertLess(ftme.cg_rmsd(cg, cg2), 10**-6)
        self.assertEqual(cg.backbone_breaks_after, cg2.backbone_breaks_after)

    def test_connected_cgs_from_pdb(self):
        cgs = ftmc.CoarseGrainRNA.from_pdb("test/forgi/threedee/data/1DUQ.pdb")
        self.assertEqual(len(cgs), 4)
        # This PDB file contains 4 similar RNA dimers
        self.assertEqual(cgs[0].name, "1DUQ_A-B")
        self.assertEqual(cgs[1].name, "1DUQ_C-D")
        self.assertEqual(cgs[2].name, "1DUQ_E-F")
        self.assertEqual(cgs[3].name, "1DUQ_G-H")
        self.assertEqual(cgs[0].defines, cgs[2].defines)
        self.assertEqual(cgs[1].defines, cgs[3].defines)

    def test_multiple_models_in_file(self):
        with self.assertWarns(UserWarning) if hasattr(self, 'assertWarns') else ignore_warnings():
            cgs = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1byj.pdb')
        self.assertEqual(len(cgs), 1)  # Only look at first model!

    def test_annotating_with_dssr(self):
        pass


class CoarseGrainTest(tfgb.GraphVerification):
    '''
    Simple tests for the BulgeGraph data structure.

    For now the main objective is to make sure that a graph is created
    and nothing crashes in the process. In the future, test cases for
    bugs should be added here.
    '''

    def setUp(self):
        self.longMessage = True

    def check_cg_integrity(self, cg):
        for s in cg.stem_iterator():
            edges = list(cg.edges[s])
            if len(edges) < 2:
                continue

            multiloops = False
            for e in edges:
                if e[0] != 'i':
                    multiloops = True

            if multiloops:
                continue

            self.assertFalse(np.allclose(cg.coords[edges[0]][0],
                                         cg.coords[edges[1]][0]))
            self.assertFalse(np.allclose(cg.coords[edges[0]][0],
                                         cg.coords[edges[1]][1]))
            self.assertFalse(np.allclose(cg.coords[edges[0]][1],
                                         cg.coords[edges[1]][0]))
            self.assertFalse(np.allclose(cg.coords[edges[0]][1],
                                         cg.coords[edges[1]][1]))

    def compare_bg_to_cg(self, bg, cg):
        for d in bg.defines.keys():
            self.assertTrue(d in cg.defines.keys())
            self.assertTrue(bg.defines[d] == cg.defines[d])

        for e in bg.edges.keys():
            self.assertTrue(e in cg.edges.keys())
            self.assertTrue(bg.edges[e] == cg.edges[e])

    def test_get_node_from_residue_num(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1X8W.pdb',
                                           load_chains='A', dissolve_length_one_stems=True)
        self.check_cg_integrity(cg)
        elem_name = cg.get_node_from_residue_num(1)
        cg.log()
        self.assertEqual(elem_name, "f0")

    def test_from_cg(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        self.check_graph_integrity(cg)
        self.check_cg_integrity(cg)

        # self.assertEqual(len(cg.coords), 8)
        for key in cg.defines.keys():
            self.assertTrue(key in cg.coords)

    def test_from_and_to_cgstring(self):
        cg1 = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        cg1.project_from = np.array([1, 2, 3.5])
        stri = cg1.to_cg_string()
        cg2 = ftmc.CoarseGrainRNA.from_bg_string(stri)

        for key in set(cg1.defines):
            self.assertTrue(key in cg2.defines)
            self.assertTrue(key in cg2.coords)
            nptest.assert_allclose(cg1.defines[key], cg2.defines[key])
            nptest.assert_allclose(cg1.coords[key][0], cg2.coords[key][0])
            nptest.assert_allclose(cg1.coords[key][1], cg2.coords[key][1])
        for key in set(cg2.defines):
            self.assertTrue(key in cg1.defines)
            self.assertTrue(key in cg1.coords)
            nptest.assert_allclose(cg1.defines[key], cg2.defines[key])
            nptest.assert_allclose(cg1.coords[key][0], cg2.coords[key][0])
            nptest.assert_allclose(cg1.coords[key][1], cg2.coords[key][1])
        nptest.assert_allclose(cg1.project_from, cg2.project_from)

    def test_to_and_from_cgstring_vres(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2mis.pdb')
        cg.add_all_virtual_residues()
        cgstri = cg.to_cg_string()
        self.assertIn("vres", cgstri)
        cg2 = ftmc.CoarseGrainRNA.from_bg_string(cgstri)
        self.assertEqual(
            len(cg2.vposs["h0"]), cg2.defines["h0"][1] - cg2.defines["h0"][0] + 1)
        self.assertLess(ftuv.vec_distance(
            cg.vposs["h0"][0], cg2.vposs["h0"][0]), 10**-8)
        self.assertLess(ftuv.vec_distance(
            cg.vposs["i0"][2], cg2.vposs["i0"][2]), 10**-8)

    def test_get_bulge_angle_stats_core(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        self.check_graph_integrity(cg)

        for d in cg.mloop_iterator():
            cg.get_bulge_angle_stats(d)

    def test_get_bulge_angle_stats_for_start(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        s1, s2 = cg.get_bulge_angle_stats("start")

    def test_read_longrange_interactions(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        self.check_graph_integrity(cg)

        self.assertGreater(len(cg.longrange), 0)

    def test_radius_of_gyration(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        self.check_graph_integrity(cg)

        rog = cg.radius_of_gyration()
        self.assertGreater(rog, 0.)

        maxDist = max(ftuv.vec_distance(p0, p1) for p0, p1 in it.combinations(cg.coords._coordinates, 2))
        estimated_radius_circum_cricle = maxDist / 2
        # NOTE: The ROG is 0.77 times the radius of the circumcircle, for m->inf many points
        # in a 3D unit sphere with the nth point placed at radius (n/m)**1/3
        self.assertLess(rog, estimated_radius_circum_cricle * 0.77)

    def test_radius_of_gyration_different_methods(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')

        rog_fast = cg.radius_of_gyration(method="fast")
        rog_vres = cg.radius_of_gyration(method="vres")
        print(rog_fast, rog_vres, rog_fast - rog_vres, file=sys.stderr)
        self.assertGreater(abs(rog_fast - rog_vres), 0, msg="Different methods for ROG calculation "
                           "producting the exactly same result? Something seems to be wrong.")
        self.assertLess(abs(rog_fast - rog_vres), 3, msg="Different methods for ROG calculation "
                        "should produce roughly the same result.")

    def test_radius_of_gyration_no_stems(self):
        cg, = ftmc.CoarseGrainRNA.from_fasta_text("AUCG\n....")
        cg.coords["f0"] = [0, 0, 0.], [12., 1, 1]
        self.assertTrue(math.isnan(cg.radius_of_gyration()))

    def test_get_sides(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1gid.cg')
        self.check_graph_integrity(cg)
        self.check_cg_integrity(cg)
        log.info(cg.to_dotbracket_string())
        log.info(cg.to_element_string(True))
        # The file 1gid.cg still starts with f1, not f0
        (s1b, s1e) = cg.get_sides('s0', 'f1')
        (s1b, s1e) = cg.get_sides('s8', 't1')

    '''
    def test_cg_from_sg(self):
        bg = ftmc.CoarseGrainRNA(
            dotbracket_str='.(((((..(((.(((((((.((.((((..((((((....))))))..)))).)).))........(((((.....((((...((((....))))...))))...))))).))))).)))...)))))')
        self.check_graph_integrity(bg)

        # bg = cgb.BulgeGraph(dotbracket_str='.(((((........)))))..((((((((.(((.((...........((((((..(((((.((((((((..(((..)))...((((....)))).....))))))))..)))))................((((((...........))))))..((...(((((((...((((((..)))))).....((......))....)))))))...(((((((((.........))))))))).(((....))).))..........(((((.(((((.......))))))))))..........))))..))............(((.((((((((...((.......))...))))))..))))).........((((((((((((..(((((((((......))))))..))).((((.......)))).....)))))..))..))).))....((...............))....))..)))))))))))...')

        for j in range(40):
            sg = bg.random_subgraph()
            new_cg = cg_from_sg(bg, sg)

            for i in it.chain(new_cg.iloop_iterator(), new_cg.mloop_iterator()):
                c = new_cg.connections(i)

                if len(c) != 2:
                    self.assertEqual(len(c), 2)
    '''

    def test_get_stem_stats(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2mis.pdb')

        cg.get_stem_stats("s0")

    def test_get_angle_stats(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2mis.pdb')
        for d in cg.defines:
            if d[0] in "mi":
                cg.get_bulge_angle_stats(d)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1byj.pdb')
        for d in cg.defines:
            if d[0] in "mi":
                cg.get_bulge_angle_stats(d)

        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2QBZ.pdb')
        for d in cg.defines:
            if d[0] in "mi":
                cg.get_bulge_angle_stats(d)

    def test_get_loop_stat(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2mis.pdb')
        cg.get_loop_stat("h0")

        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/4GXY_A.cg')  # Contains a loop with r=0
        self.check_graph_integrity(cg)
        self.check_cg_integrity(cg)
        cg.get_loop_stat('h3')

    def test_length_one_stems(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1byj.pdb',
                                           remove_pseudoknots=False)
        self.check_graph_integrity(cg)
        self.check_cg_integrity(cg)

        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2QBZ.pdb',
                                           remove_pseudoknots=False)
        self.check_graph_integrity(cg)
        self.check_cg_integrity(cg)

    def test_pseudoknot(self):
        #cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/1ymo.pdb')
        # self.check_graph_integrity(cg)
        # self.check_cg_integrity(cg)

        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/3D0U_A.cg')
        self.check_graph_integrity(cg)
        self.check_cg_integrity(cg)

        cg.traverse_graph()
        self.assertEqual(cg.get_angle_type("i3"), 1)

    def test_small_molecule(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2X1F.pdb')
        log.info(cg.to_dotbracket_string())
        log.info(cg.to_element_string(True))
        log.info("COORDS {}".format(cg.coords))
        self.assertTrue('f0' in cg.coords)

    def test_longrange_iterator(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')

        interactions = list(cg.longrange_iterator())

        self.assertEqual(len(interactions), 4)
        self.assertTrue(('i0', 's0') in interactions)

    def test_longrange_distance(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')

        dist = cg.element_physical_distance('h0', 'h1')

        self.assertTrue(dist < 10)

    def test_total_length(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        self.assertEqual(cg.total_length(), cg.seq_length)
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2X1F.pdb')
        self.assertEqual(cg.total_length(), cg.seq_length)
        cg = ftmc.CoarseGrainRNA.from_dotbracket('..((..((...))..))..((..))..')
        self.assertEqual(cg.total_length(), cg.seq_length)
        self.assertEqual(cg.total_length(), 27)

    def test_get_load_coordinates(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        coords = cg.get_coordinates_array()
        new_cg = copy.deepcopy(cg)
        for key in new_cg.coords:
            new_cg.coords[key] = [0, 0, 0], [0, 0, 0]

        new_cg.load_coordinates_array(coords)
        for key in new_cg.coords:
            for i in range(len(new_cg.coords[key])):
                nptest.assert_allclose(new_cg.coords[key][i],
                                       cg.coords[key][i])
    """
    def test_is_stacking(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file('test/forgi/threedee/data/3way.cg')
        self.assertFalse(cg.is_stacking("m0")) #Distance
        self.assertFalse(cg.is_stacking("m1")) #distance
        self.assertFalse(cg.is_stacking("m2")) #shear angle
    def test_is_stacking2(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file('test/forgi/threedee/data/1I9V_noPK.cg')
        self.assertFalse(cg.is_stacking("m0"))
        self.assertFalse(cg.is_stacking("m2"))
        self.assertTrue(cg.is_stacking("m1"))
    """

    def test_coords_from_direction(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1I9V_noPK.cg')
        cg_old = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1I9V_noPK.cg')
        coords = cg.get_coordinates_array()
        directions = coords[1::2] - coords[0::2]
        cg._init_coords()
        cg.coords_from_directions(directions)
        # self.assertAlmostEqual(ftme.cg_rmsd(cg, cg_old), 0) #This only looks at stems
        # The coordinates should be the same as before except for a constant offset
        new_coords = cg.get_coordinates_array()
        offset = (coords - new_coords)
        print(offset)
        # I use allclose, because it uses broadcasting
        assert np.allclose(offset, offset[0])

    def test_coords_from_direction_with_pseudoknot(self):
        # This tests the case where the link is inserted from reverse direction.
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/3D0U_A.cg')
        cg_old = copy.deepcopy(cg)

        coords = cg.get_coordinates_array()
        directions = cg.coords_to_directions()

        cg._init_coords()
        cg.twists = cg_old.twists
        log.info("len(coords):{}, len(directions):{}, len(defines):{}".format(
            len(coords), len(directions), len(cg.defines)))

        cg.coords_from_directions(directions)
        self.assertLess(ftme.cg_rmsd(cg, cg_old), 10**-6)
        new_coords = cg.get_coordinates_array()
        offset = (coords - new_coords)
        assert np.allclose(offset, offset[0])

    @unittest.skip("It is hard to do the subgraph thing correctly in a way consistent with the RNA model. Thus it has been disabled in the current release!")
    def test_cg_from_sg_invalid_subgraph_breaking_m(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/3D0U_A.cg')
        """
             /s3 --h1
           m1  |
          /    |
        s0     m2
          \    |
           m0  |
             \s1--i0--s2--h0
        """
        split_ml = ["s0", "m0", "s1"]
        with self.assertRaises(Exception):
            ftmc.cg_from_sg(cg, split_ml)

    @unittest.skip("It is hard to do the subgraph thing correctly in a way consistent with the RNA model. Thus it has been disabled in the current release!")
    def test_cg_from_sg_breaking_after_i(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/3D0U_A.cg')
        """
             /s3 --h1
           m1  |
          /    |
        s0     m2
          \    |
           m0  |
             \s1--i0--s2--h0
        """
        split_ml = ["s0", "m0", "s1", "m2", "s3", "m1", "h1", "i0"]

        sg = ftmc.cg_from_sg(cg, split_ml)
        self.check_graph_integrity(sg)

    @unittest.skip("It is hard to do the subgraph thing correctly in a way consistent with the RNA model. Thus it has been disabled in the current release!")
    def test_cg_from_sg_breaking_after_s(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/3D0U_A.cg')
        """
             /s3 --h1
           m1  |
          /    |
        s0     m2
          \    |
           m0  |
             \s1--i0--s2--h0
        """
        split_ml = ["s0", "m0", "s1", "m2", "s3", "m1", "h1"]

        sg = ftmc.cg_from_sg(cg, split_ml)
        self.check_graph_integrity(sg)


class TestVirtualAtoms(unittest.TestCase):
    def setUp(self):
        self.longMessage = True

    @unittest.skip("This test currently fails. Should be fixed in version 0.5")
    def test_virtual_atoms_only_single_stranded(self):
        cg, = ftmc.CoarseGrainRNA.from_pdb('test/forgi/threedee/data/2X1F.pdb')
        va = cg.virtual_atoms(1)
        self.assertIn("C1'", va)  # C1' should be always present

    def test_virtual_atoms_stem_distance_to_pairing_partner(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        va1 = cg.virtual_atoms(1)
        va2 = cg.virtual_atoms(cg.pairing_partner(1))
        self.assertLess(ftuv.vec_distance(
            va1["C1'"], va2["C1'"]), 25, msg="Virtual atoms too far apart")
        self.assertGreater(ftuv.vec_distance(
            va1["C1'"], va2["C1'"]), 8, msg="Virtual atoms too close")

    def test_virtual_atoms_stem_distance_to_stacked_base(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        va1 = cg.virtual_atoms(1)
        va2 = cg.virtual_atoms(2)
        self.assertLess(ftuv.vec_distance(
            va1["C1'"], va2["C1'"]), 10, msg="Virtual atoms too far apart")
        self.assertGreater(ftuv.vec_distance(
            va1["C1'"], va2["C1'"]), 2, msg="Virtual atoms too close")

    def test_virtuel_atom_caching_is_reset(self):
        cg = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        va_old = cg.virtual_atoms(1)["C1'"]
        # Stay orthogonal to twists
        cg.coords["s0"] = cg.coords["s0"][0] + \
            (cg.coords["s0"][1] - cg.coords["s0"][0]) * 0.5, cg.coords["s0"][1]
        va_new = cg.virtual_atoms(1)["C1'"]
        self.assertTrue(np.any(np.not_equal(va_old, va_new)),
                        msg="A stale virtual atom position was used.")


class RotationTranslationTest(unittest.TestCase):
    def setUp(self):
        self.cg1 = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.cg2, = ftmc.CoarseGrainRNA.from_pdb(
                        'test/forgi/threedee/data/1byj.pdb')

    def test_rotate_keeps_RMSD_zero0(self):
        cg1_rot = copy.deepcopy(self.cg1)
        cg1_rot.rotate(30, unit="degrees")
        cg1_rot.rotate(-30, unit="degrees")
        self.assertLess(ftme.cg_rmsd(self.cg1, cg1_rot), 10**-6)

    def test_rotate_keeps_RMSD_zero(self):
        cg1_rot = copy.deepcopy(self.cg1)
        cg1_rot.rotate(30, unit="degrees")
        # This currently uses virtual atoms, thus takes twists into account.
        self.assertLess(ftme.cg_rmsd(self.cg1, cg1_rot), 10**-6)


        cg2_rot = copy.deepcopy(self.cg2)
        cg2_rot.rotate(45, unit="degrees")

        a,b = self.cg2.get_ordered_virtual_residue_poss(True)
        log.warning("------------------------")
        c,d = cg2_rot.get_ordered_virtual_residue_poss(True)
        c2 = np.dot(c, ftuv.rotation_matrix("x", math.radians(-45)).T)
        log.warning("==================================")
        for i, coord in enumerate(a):
            if any(abs(coord-c2[i])>10**-4):
                log.warning("{} {} {} {}".format(coord, b[i], c2[i], d[i]))

        self.assertLess(ftme.cg_rmsd(self.cg2, cg2_rot), 10**-6)


class StericValueTest(unittest.TestCase):
    def setUp(self):
        self.cg1 = ftmc.CoarseGrainRNA.from_bg_file(
            'test/forgi/threedee/data/1y26.cg')
        self.cg2, = ftmc.CoarseGrainRNA.from_pdb(
            'test/forgi/threedee/data/1byj.pdb')

    @unittest.skip("Manual test")
    def test_stericValue_1(self):
        print("m0, m1, m2", self.cg1.steric_value(["m0", "m1", "m2"]))
        from_ = np.amin(self.cg1.coords._coordinates)
        to_ = np.amax(self.cg1.coords._coordinates)
        x, y, z = np.mgrid[from_:to_:4, from_:to_:4, from_:to_:4]
        from mayavi import mlab
        s = np.zeros_like(x)
        for i, j, k in np.ndindex(x.shape):
            s[i, j, k] = self.cg1.steric_value(
                np.array([x[i, j, k], y[i, j, k], z[i, j, k]]), "r**-3")
        #mlab.contour3d(x,y,z,s, contours= [0.5, 1, 2, 5], opacity=0.3)
        src = mlab.pipeline.scalar_field(x, y, z, s)
        mlab.pipeline.volume(src)
        #mlab.pipeline.iso_surface(src, contours=[0.1, ], opacity=0.3)
        #mlab.pipeline.iso_surface(src, contours=[0.5, ], opacity=0.7)
        #mlab.pipeline.iso_surface(src, contours=[1, ])

        colors = {"s": (0, 1, 0), "h": (0, 0, 1), "m": (1, 0, 0), "i": (
            1, 1, 0), "f": (0.5, 0.5, 0.5), "t": (0.5, 0.5, 0.5)}
        for d in self.cg1.defines:
            x = self.cg1.coords[d][0][0], self.cg1.coords[d][1][0]
            y = self.cg1.coords[d][0][1], self.cg1.coords[d][1][1]
            z = self.cg1.coords[d][0][2], self.cg1.coords[d][1][2]
            mlab.plot3d(x, y, z, tube_radius=2, color=colors[d[0]])
        mlab.show()
        assert False
