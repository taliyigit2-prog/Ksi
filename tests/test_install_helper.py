import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('install_helper',ROOT/'.github/release-tools/create_install_helper.py')
HELPER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def fixture():
    return dict(ready_to_publish=True,version='2.0.0',architectures={
        architecture:dict(files=[dict(filename='KSI-Local-Studio-2.0.0-'+architecture+'.dmg',
                                    sha256=hashlib.sha256(b'x').hexdigest(),size=1)])
        for architecture in ('arm64','x86_64')})


class InstallHelperTests(unittest.TestCase):
    def test_gate_failure_creates_no_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'installer.command'
            with patch.object(HELPER,'verify_release',side_effect=ValueError('not accepted')):
                with self.assertRaises(ValueError):
                    HELPER.create({},'a'*40,output)
            self.assertFalse(output.exists())

    def test_unaccepted_or_development_report_rejected(self):
        for change in (dict(ready_to_publish=False),dict(version='2.0.0.dev0'),dict(architectures={})):
            report=fixture();report.update(change)
            with self.assertRaises(ValueError):
                HELPER.render(report,dict(arm64=1,x86_64=1))

    def test_untrusted_filename_and_size_cannot_inject_shell(self):
        for key,value in (('filename','malicious;command.dmg'),('size',True),('sha256','not-a-digest')):
            report=fixture();report['architectures']['arm64']['files'][0][key]=value
            with self.assertRaises(ValueError):
                HELPER.render(report,dict(arm64=1,x86_64=1))

    def test_generated_shell_compiles_and_uses_only_public_tokenless_downloads(self):
        text=HELPER.render(fixture(),dict(arm64=1,x86_64=1))
        subprocess.run(['/bin/bash','-n'],input=text.encode(),check=True,timeout=30)
        self.assertIn('curl -q --noproxy',text)
        self.assertIn('shasum -a 256',text)
        self.assertIn('hw.optional.arm64',text)
        self.assertNotIn('sudo',text)
        self.assertNotIn('xattr -d',text)
        self.assertNotIn('rm -rf',text)
        self.assertNotIn('GH_TOKEN',text)

    def test_verify_only_checks_owned_tiny_synthetic_parts_without_network(self):
        text=HELPER.render(fixture(),dict(arm64=1,x86_64=1))
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='ksi-installer-unit-') as directory:
            root=Path(directory).resolve()
            for architecture in ('arm64','x86_64'):
                (root/('KSI-Local-Studio-2.0.0-'+architecture+'.dmg')).write_bytes(b'x')
            result=subprocess.run(['/bin/bash','-c',text,'ksi-synthetic-installer','--verify-only',str(root)],
                                  capture_output=True,text=True,timeout=60,env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(root)))
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            self.assertIn('SHA-256',result.stdout)
            for path in root.glob('*.dmg'):
                path.write_bytes(b'corrupt')
            failure=subprocess.run(['/bin/bash','-c',text,'ksi-synthetic-installer','--verify-only',str(root)],
                                   capture_output=True,text=True,timeout=60,env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(root)))
            self.assertNotEqual(failure.returncode,0)
            self.assertTrue(all(path.read_bytes()==b'corrupt' for path in root.glob('*.dmg')))
