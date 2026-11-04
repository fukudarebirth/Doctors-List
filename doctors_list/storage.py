"""Private local JSON; never deserialize executable formats or write inside checkout."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 20 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def private_path(path):
    path = Path(path).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('個人情報・認証情報の保存先はリポジトリ外を指定してください。')
    return path


def load_json(path):
    path = Path(path).expanduser()
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('JSONが20 MiBを超えています。分割してください。')
    def unique_pairs(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('JSONキーが重複しています。')
            result[k] = v
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)


def save_private_json(path, value):
    """Atomic local replace + readback hash; caller controls when authoritative sync is allowed."""
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    fd, tmp = tempfile.mkstemp(prefix='.doctors-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(load_json(tmp)) != digest(value):
            raise OSError('一時ファイルの保存検証に失敗しました。')
        os.replace(tmp, path)
        if digest(load_json(path)) != digest(value):
            raise OSError('保存後の再読込検証に失敗しました。')
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {'verified': True, 'sha256': digest(value)}
