"""Fork tests. Run them with: python -m unittest discover -s webui/tests -t .

Test dependencies are listed in webui/requirements-sync-ci.txt. A test module that
needs a package which is not installed is skipped; set NX_REQUIRE_TEST_DEPS=1 (CI does)
to make a missing package an error instead, so a broken install cannot hide the tests.
"""
import importlib
import os
import unittest


def require(*modules):
    """Call at the top of a test module that imports optional test dependencies."""
    for name in modules:
        try:
            importlib.import_module(name)
        except ImportError as error:
            if os.environ.get('NX_REQUIRE_TEST_DEPS') == '1':
                raise
            raise unittest.SkipTest(f'{name} is not installed (pip install -r webui/requirements-sync-ci.txt)') from error
