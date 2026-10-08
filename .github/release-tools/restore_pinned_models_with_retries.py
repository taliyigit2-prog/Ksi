"""Retry only transient public-source outages without changing any product pin."""
import argparse
import http.client
import json
import socket
import time
import urllib.error
from pathlib import Path
from ksi_local.model_input_restore import restore_model_inputs


def transient(error):
    if isinstance(error,urllib.error.HTTPError):
        return error.code in {408,429,500,502,503,504}
    if isinstance(error,urllib.error.URLError):
        return isinstance(error.reason,(TimeoutError,ConnectionError,socket.gaierror))
    return isinstance(error,(TimeoutError,ConnectionError,http.client.IncompleteRead))


def restore(root,models,ollama,*,allow_network=False):
    if not allow_network:
        raise ValueError('Public build-input restoration needs explicit network authorization')
    for attempt in range(1,5):
        try:
            result=restore_model_inputs(root,models,ollama,allow_network=True)
            return dict(result,download_attempts=attempt)
        except Exception as error:
            if not transient(error) or attempt==4:
                raise
            # Never log exception URLs, redirect signatures, tokens or paths.
            print(json.dumps(dict(transient_download_failure=type(error).__name__,
                                  attempt=attempt,retry_delay_seconds=attempt*10)),flush=True)
            time.sleep(attempt*10)
    raise AssertionError('Unreachable retry boundary')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository',type=Path)
    parser.add_argument('imported_inputs',type=Path)
    parser.add_argument('--allow-network',action='store_true')
    args=parser.parse_args()
    models=json.loads((args.repository/'config/model-sources.json').read_bytes())
    ollama=json.loads((args.repository/'config/ollama-model-sources.json').read_bytes())
    print(json.dumps(restore(args.imported_inputs.absolute(),models,ollama,allow_network=args.allow_network)))
