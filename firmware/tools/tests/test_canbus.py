"""nemadbg.canbus の libusb PATH 補助の試験（libusb_package は偽物に差し替える）。"""

import os
import sys
import types
import unittest
from unittest import mock

from nemadbg import canbus

DLL_DIR = os.path.join(os.sep, "fake", "libusb")


def _fake_module(path):
    m = types.ModuleType("libusb_package")
    m.get_library_path = lambda: path
    return m


class AddLibusbToPathTest(unittest.TestCase):
    def test_prepends(self):
        with mock.patch.dict(os.environ, {"PATH": "X"}), \
                mock.patch.dict(sys.modules, {"libusb_package": _fake_module(os.path.join(DLL_DIR, "libusb-1.0.dll"))}):
            canbus._add_libusb_to_path()
            self.assertEqual(os.environ["PATH"], DLL_DIR + os.pathsep + "X")

    def test_not_twice(self):
        with mock.patch.dict(os.environ, {"PATH": "X"}), \
                mock.patch.dict(sys.modules, {"libusb_package": _fake_module(os.path.join(DLL_DIR, "libusb-1.0.dll"))}):
            canbus._add_libusb_to_path()
            canbus._add_libusb_to_path()
            self.assertEqual(os.environ["PATH"].split(os.pathsep).count(DLL_DIR), 1)

    def test_missing_module_ignored(self):
        with mock.patch.dict(os.environ, {"PATH": "X"}), \
                mock.patch.dict(sys.modules, {"libusb_package": None}):
            canbus._add_libusb_to_path()
            self.assertEqual(os.environ["PATH"], "X")

    def test_none_path_ignored(self):
        with mock.patch.dict(os.environ, {"PATH": "X"}), \
                mock.patch.dict(sys.modules, {"libusb_package": _fake_module(None)}):
            canbus._add_libusb_to_path()
            self.assertEqual(os.environ["PATH"], "X")


if __name__ == "__main__":
    unittest.main()
