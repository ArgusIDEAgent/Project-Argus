import base64
import hashlib
import io
import json
import tarfile
import threading
from unittest.mock import patch

import pytest

from safe_runner.dependencies import Archive, locked_packages, download, RegistryConnection
from safe_runner.contracts import RunnerError

PIN = {'name':'is-number','version':'7.0.0','url':'https://registry.npmjs.org/is-number/-/is-number-7.0.0.tgz',
       'integrity':'sha512-41Cifkg6e8TylSpdtTpeLVMqvSBEVzTttHvERD741+pnZ8ANv0004MRL43QKPDlK9cGvNp6NZWZUBlbGXYxxng==','dependencies':{}}


def manifest():
    package = {'dependencies':{'is-number':'7.0.0'}}
    lock = {'lockfileVersion':3,'packages':{'':package,'node_modules/is-number':{
        'version':PIN['version'],'resolved':PIN['url'],'integrity':PIN['integrity']}}}
    return package, lock


def archive(entries=None, manifest_extra=None):
    buffer = io.BytesIO()
    contents = [('package/package.json', json.dumps({'name':'fixture-pkg','version':'1.0.0',**(manifest_extra or {})}).encode(), None)] + (entries or [('package/index.js', b'module.exports=42;', None)])
    with tarfile.open(fileobj=buffer,mode='w:gz',format=tarfile.USTAR_FORMAT) as tar:
        for name, data, kind in contents:
            info=tarfile.TarInfo(name); info.size=len(data)
            if kind:
                info.type=kind; info.linkname='/etc/passwd'; info.size=0
            tar.addfile(info, None if kind else io.BytesIO(data))
    raw=buffer.getvalue()
    meta={'name':'fixture-pkg','version':'1.0.0','url':'https://registry.npmjs.org/fixture-pkg/-/fixture-pkg-1.0.0.tgz',
          'integrity':'sha512-'+base64.b64encode(hashlib.sha512(raw).digest()).decode(),'dependencies':{}}
    return Archive(json.dumps(meta,sort_keys=True),raw)


def test_lock_pins_exact_dependency_and_transitive_closure():
    package, lock=manifest()
    assert locked_packages(package,lock)==[PIN]


@pytest.mark.parametrize('change', ['url','hash','range','root','hook','link','nested','extra','optional'])
def test_unsupported_lock_inputs_are_rejected(change):
    package, lock=manifest(); entry=lock['packages']['node_modules/is-number']
    if change=='url': entry['resolved']='http://127.0.0.1/credentials'
    if change=='hash': entry['integrity']='sha1-ignored'
    if change=='range': package['dependencies']['is-number']='^7.0.0'
    if change=='root': lock['packages']['']={}
    if change=='hook': entry['hasInstallScript']=True
    if change=='link': entry['link']=True
    if change=='nested': lock['packages']['node_modules/a/node_modules/is-number']=lock['packages'].pop('node_modules/is-number')
    if change=='extra': package['dependencies']={}
    if change=='optional': entry['optional']=True
    with pytest.raises(ValueError): locked_packages(package,lock)


def test_integrity_and_cancel_fail_without_installation():
    with pytest.raises(ValueError): Archive(json.dumps(PIN),b'corrupted')
    cancel=threading.Event(); cancel.set()
    with patch('safe_runner.dependencies.RegistryConnection') as client:
        with pytest.raises(RunnerError): download([PIN],cancel)
        client.assert_not_called()


def test_private_registry_resolution_is_never_connected():
    for address in ('127.0.0.1','169.254.169.254','10.0.0.1'):
        with patch('safe_runner.dependencies.socket.getaddrinfo',return_value=[(None,None,None,None,(address,443))]), patch('safe_runner.dependencies.socket.create_connection') as connect:
            with pytest.raises(RunnerError): RegistryConnection('registry.npmjs.org').connect()
            connect.assert_not_called()


def test_registry_redirects_are_refused_and_connection_closed():
    from unittest.mock import MagicMock
    client=MagicMock(); client.getresponse.return_value.status=302
    with patch('safe_runner.dependencies.RegistryConnection',return_value=client):
        with pytest.raises(RunnerError): download([PIN],threading.Event())
    client.close.assert_called_once()


@pytest.mark.parametrize('kind', ['corrupt', 'oversized'])
def test_download_integrity_and_size_budgets(kind):
    from unittest.mock import MagicMock
    client=MagicMock(); reply=client.getresponse.return_value
    reply.status=200; reply.getheader.return_value='identity'
    reply.read1.side_effect=([b'bad',b''] if kind=='corrupt' else [b'x'*16384]*17)
    with patch('safe_runner.dependencies.RegistryConnection',return_value=client):
        with pytest.raises((ValueError,RunnerError)): download([PIN],threading.Event())
    args, kwargs=client.request.call_args
    assert args[:2]==('GET','/is-number/-/is-number-7.0.0.tgz')
    assert set(kwargs['headers'])=={'Accept-Encoding','User-Agent'}
    client.close.assert_called_once()
