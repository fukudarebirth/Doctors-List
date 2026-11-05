"""Shared Windows/CLI workflow. Configuration paths contain no credential values."""
from pathlib import Path
from .google_reader import build_service
from .sheets_write import GoogleBackend
from .storage import private_path


def write_token_path(config):
    readonly = private_path(config['token_path'])
    token = private_path(config.get('write_token_path') or readonly.with_name('token-write.json'))
    if token in (readonly, private_path(config['client_secrets_path'])):
        raise ValueError('書き込み用トークンは読み取り用トークン・クライアントとは別ファイルが必要です。')
    return token


def backend(config, writable=False, interactive=False):
    token = write_token_path(config) if writable else config['token_path']
    service = build_service(config['client_secrets_path'], token, interactive=interactive, writable=writable)
    return GoogleBackend(service, config, writable=writable)


def state_directory(config):
    return private_path(Path(config['token_path']).parent / 'operations')
