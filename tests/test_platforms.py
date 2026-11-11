import unittest
from droidforge.core import ForgeError
from droidforge.platforms import package_commands, normalize_arch


class PlatformTests(unittest.TestCase):
    def test_debian_and_kali_packages(self):
        for distro in ({'ID': 'debian'}, {'ID': 'kali', 'ID_LIKE': 'debian'}):
            plan = package_commands(distro)
            self.assertEqual(plan[0], ['apt-get', 'update'])
            self.assertIn('libudev-dev', plan[1])
            self.assertIn('build-essential', plan[1])

    def test_arch_packages_do_not_perform_partial_or_full_upgrade(self):
        plan = package_commands({'ID': 'arch'})
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0][:4], ['pacman', '-S', '--needed', '--noconfirm'])
        self.assertIn('systemd-libs', plan[0])
        self.assertIn('base-devel', plan[0])

    def test_unsupported_distribution_has_actionable_failure(self):
        with self.assertRaises(ForgeError): package_commands({'ID': 'unknown'})

    def test_normalizes_linux_cpu_names(self):
        for name in ('aarch64', 'arm64'):
            self.assertEqual(normalize_arch(name), 'arm64')
        for name in ('x86_64', 'amd64'):
            self.assertEqual(normalize_arch(name), 'x86_64')
        with self.assertRaises(ForgeError): normalize_arch('riscv64')

    def test_host_validation_requires_linux_glibc(self):
        from droidforge.platforms import validate_host
        self.assertEqual(validate_host('Linux', 'aarch64', 'glibc'), 'arm64')
        self.assertEqual(validate_host('Linux', 'x86_64', 'glibc'), 'x86_64')
        for system, machine, libc in [('Linux', 'aarch64', 'musl'), ('Windows', 'AMD64', '')]:
            with self.subTest(system=system, libc=libc), self.assertRaises(ForgeError):
                validate_host(system, machine, libc)

    def test_legacy_cache_architecture_is_checked(self):
        import tempfile
        from pathlib import Path
        from droidforge.platforms import verify_cache_arch
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            system = state / 'build/CMakeFiles/4.3/CMakeSystem.cmake'
            system.parent.mkdir(parents=True)
            system.write_text('set(CMAKE_SYSTEM_PROCESSOR "x86_64")\n')
            with self.assertRaises(ForgeError): verify_cache_arch(state, 'arm64')
            verify_cache_arch(state, 'x86_64')
            system.unlink()
            with self.assertRaises(ForgeError): verify_cache_arch(state, 'x86_64')
