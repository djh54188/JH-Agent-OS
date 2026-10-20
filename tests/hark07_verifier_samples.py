"""Planted outcomes for tests/test_hark07_verifier_classification.py.

Not a test module (no test_ prefix), so discovery never runs these; the
classification tests load them by name on purpose.
"""
import unittest


class _Samples(unittest.TestCase):
    def test_pass(self):
        pass

    def test_assert(self):
        self.fail("planted")

    def test_error(self):
        raise RuntimeError("planted")

    @unittest.skip("planted")
    def test_skip(self):
        pass


class _Empty(unittest.TestCase):
    pass
