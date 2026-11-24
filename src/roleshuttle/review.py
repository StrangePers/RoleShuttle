"""Inspect a Git worktree against a controller-pinned revision and file scope."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading

from .scope import check_scope, safe_path as _safe_path

MAX_FILES = 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DIFF_BYTES = 1 * 1024 * 1024
_SECRET = re.compile(
    r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|'
    r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|'
    r'AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{20,})\b|'
    r'(?i:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*[\"\x27]?[^\s\"\x27]{16,}'
)


def redact(value):
    return _SECRET.sub('[REDACTED SECRET]', value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _review_diff_limit():
    """Read operator configuration at use time, never from a worker contract."""
    raw = os.environ.get('ROLESHUTTLE_REVIEW_DIFF_BYTES')
    if raw is None:
        return MAX_DIFF_BYTES
    if re.fullmatch(r'[0-9]+', raw):
        try:
            value = int(raw)
            if 64 * 1024 <= value <= 8 * 1024 * 1024:
                return value
        except ValueError:
            pass  # includes Python's limit on excessively long integer strings
    raise ValueError('ROLESHUTTLE_REVIEW_DIFF_BYTES must be an ASCII decimal integer '
                     'from 65536 to 8388608 bytes; unset it for the 1048576-byte default')


def _line_endings(text):
    pairs = text.count('\r\n')
    return {'crlf': pairs, 'lf': text.count('\n') - pairs, 'bare_cr': text.count('\r') - pairs}


def _display_diff(name, old, new):
    """Compact CRLF display only; manifests and safety checks use original bytes."""
    endings = None
    prefix = ''
    if '\r\n' in old or '\r\n' in new:
        endings = {'path': name, 'base': _line_endings(old), 'current': _line_endings(new),
                   'only_line_endings': old != new and old.replace('\r\n', '\n') == new.replace('\r\n', '\n')}
        prefix = f"# {name}: CRLF displayed as LF; raw-byte fingerprint unchanged by display. "
        prefix += f"Line endings base={endings['base']} current={endings['current']}"
        prefix += '; only line endings changed.\n' if endings['only_line_endings'] else '.\n'
    # Do not strip spaces, bare CR, or final-newline differences.
    old_lines = old.replace('\r\n', '\n').splitlines(keepends=True)
    new_lines = new.replace('\r\n', '\n').splitlines(keepends=True)
    chunks = difflib.unified_diff(old_lines, new_lines, fromfile='a/' + name, tofile='b/' + name)
    part = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of line\n' for line in chunks)
    return prefix + part, endings


def _git(root, *args, limit=MAX_FILE_BYTES):
    """Bound output even for a hostile repository, disabling external diff hooks."""
    process = subprocess.Popen(
        ['git', '-c', 'safe.directory=' + str(root), '-c', 'core.quotePath=false',
         '-C', str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    chunks = []
    size = 0
    overflow = threading.Event()

    def read():
        nonlocal size
        while chunk := process.stdout.read(65536):
            size += len(chunk)
            if size > limit:
                overflow.set()
                process.kill()
                break
            chunks.append(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise ValueError('Git inspection timed out') from None
    finally:
        reader.join(2)
        process.stdout.close()
    if overflow.is_set():
        raise ValueError('Repository inspection exceeds bounded size')
    if code:
        raise ValueError('Git inspection failed: ' + args[0])
    return b''.join(chunks)


def _names(root, *args, limit=None):
    if limit is None:
        limit = _review_diff_limit()
    values = _git(root, *args, limit=limit).decode('utf-8', errors='strict').split('\0')
    return [p for p in values if p]


def repository_head(root):
    """Return the full current commit of this Git worktree root."""
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").rstrip(b"\r\n"))).resolve()
    if top != root:
        raise ValueError("Register the Git worktree root")
    return _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()


def _inside(root, value):
    path = root
    for piece in value.rstrip('/').split('/'):
        path = path / piece
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('path traverses a symlink/junction')
    if not path.resolve().is_relative_to(root):
        raise ValueError('path escapes worktree')
    return path


def _generated(root, contract, values, *, diff_limit=None):
    if not isinstance(values, (list, tuple)):
        raise ValueError('generated_paths must be a list of literal directories')
    paths = []
    for value in values:
        path = _safe_path(value, directory=True)
        if not path.endswith('/'):
            raise ValueError('generated outputs must be directories ending in /')
        for allowed in contract['allowed_paths']:
            rule = _safe_path(allowed, directory=True)
            if path.rstrip('/') == rule.rstrip('/') or path.startswith(rule.rstrip('/') + '/') or rule.startswith(path):
                raise ValueError('generated output overlaps allowed source paths')
        # Query the exact entry, without recursing or listing unrelated files.
        # Omitting the trailing slash finds both a blob and a tree at this name;
        # a slash would descend into a tree and miss an exact blob collision.
        if _names(root, 'ls-tree', '--name-only', '-z', contract['base_revision'],
                  '--', ':(literal)' + path.rstrip('/'), limit=diff_limit):
            raise ValueError('generated output already contains base files')
        _inside(root, path)
        paths.append(path)
    return paths


def snapshot(root, contract, generated_paths=()):
    """Inspect actual bytes, never paths or PASS supplied by a worker/browser."""
    diff_limit = _review_diff_limit()
    supplied_root = Path(root).absolute()
    if supplied_root.is_symlink() or getattr(supplied_root, 'is_junction', lambda: False)():
        raise ValueError('Worktree root cannot be a symlink/junction')
    root = supplied_root.resolve(strict=True)
    base = contract['base_revision']
    if not isinstance(base, str) or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', base):
        raise ValueError('Full pinned base commit required')
    head = _git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
    actual_root = Path(_git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if actual_root != root:
        raise ValueError('Expected the actual worktree root')
    generated = _generated(root, contract, generated_paths, diff_limit=diff_limit)
    index = _git(root, 'ls-files', '--stage', '-z', limit=MAX_TOTAL_BYTES)
    flags = _git(root, 'ls-files', '-v', '-z', limit=MAX_TOTAL_BYTES)
    staged = _names(root, 'diff', '--cached', '--no-ext-diff', '--no-textconv', '--name-only', '-z', base, '--', limit=diff_limit)
    changed = set(_names(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', base, '--', limit=diff_limit))
    changed.update(_names(root, 'ls-files', '--others', '-z', limit=diff_limit))
    if len(changed) > MAX_FILES:
        raise ValueError('Too many changed files to review safely')
    base_modes = {}
    for record in _git(root, 'ls-tree', '-r', '-z', base, limit=MAX_TOTAL_BYTES).split(b'\0'):
        if record:
            info, name = record.split(b'\t', 1)
            mode, kind, oid = info.decode().split(' ')
            base_modes[name.decode('utf-8')] = (mode, kind, oid)
    violations = []
    if head != base:
        violations.append('worker changed HEAD')
    if staged:
        violations.append('worker changed Git index; staging is forbidden')
    if any(record and (record[:1].islower() or record[:1] == b'S') for record in flags.split(b'\0')):
        violations.append('Git index hides tracked files with assume-unchanged/skip-worktree flags')
    files, manifests, diff_parts, line_endings = [], [], [], []
    total = 0
    diff_bytes = 0
    total_diff_bytes = 0
    truncated = False
    for name in sorted(changed):
        try:
            value = _safe_path(name)
            path = _inside(root, value)
        except (ValueError, OSError, RuntimeError) as exc:
            files.append(name)
            manifests.append({'path':name, 'invalid':True})
            violations.append(name + ': ' + str(exc))
            continue
        if any(value.startswith(prefix) for prefix in generated):
            continue
        files.append(value)
        scope = check_scope(root, [value], contract['allowed_paths'])
        if scope:
            violations.extend(scope)
            manifests.append({'path':value, 'outside_scope':True})
            continue  # never read/display unauthorized file contents
        metadata = base_modes.get(value)
        if metadata and metadata[1] != 'blob':
            violations.append(value + ': non-blob Git entries are unsupported')
            manifests.append({'path':value, 'unsupported':True})
            continue
        try:
            prior = _git(root, 'cat-file', 'blob', metadata[2]) if metadata else b''
            current = b''
            mode = None
            if path.exists():
                before = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValueError('not a bounded regular file')
                if before.st_nlink > 1:
                    raise ValueError('hard-linked changed files require external review')
                with path.open('rb') as stream:
                    current = stream.read(MAX_FILE_BYTES + 1)
                after = path.stat(follow_symlinks=False)
                if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino):
                    raise ValueError('file changed during inspection')
                if len(current) > MAX_FILE_BYTES:
                    raise ValueError('file exceeds review size')
                mode = stat.S_IMODE(after.st_mode)
            total += len(prior) + len(current)
            if total > MAX_TOTAL_BYTES:
                raise ValueError('total content exceeds review size')
            manifests.append({'path':value, 'sha256':hashlib.sha256(current).hexdigest(),
                              'mode':mode, 'base_mode': metadata[0] if metadata else None})
            old_text = prior.decode('utf-8', errors='strict')
            new_text = current.decode('utf-8', errors='strict')
            if '\0' in old_text or '\0' in new_text:
                raise ValueError('binary changes require external review')
            if _SECRET.search(old_text) or _SECRET.search(new_text):
                violations.append(value + ': possible secret material; diff withheld')
                continue
            part, endings = _display_diff(value, old_text, new_text)
            if endings:
                line_endings.append(endings)
            encoded = part.encode('utf-8')
            total_diff_bytes += len(encoded)
            if diff_bytes + len(encoded) > diff_limit:
                truncated = True
                continue
            diff_parts.append(part)
            diff_bytes += len(encoded)
        except (OSError, ValueError, UnicodeError) as exc:
            violations.append(value + ': ' + str(exc))
    if contract.get('mode') == 'read' and files:
        violations.append('read-only task changed files')
    if truncated:
        violations.append(f'diff exceeds review display limit (actual {total_diff_bytes} bytes; '
                          f'configured {diff_limit} bytes)')
    binding = {'task_id': contract['task_id'], 'base': base, 'head': head,
               'index': hashlib.sha256(index + flags).hexdigest(), 'files': manifests}
    context = []
    for value in contract.get('review_paths', []):
        try:
            name = _safe_path(value)
            path = _inside(root, name)
            entry = path.stat()
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or entry.st_size > MAX_FILE_BYTES:
                raise ValueError('expected a bounded regular review file')
            with path.open('rb') as stream:
                context.append({'path': name, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
        except (OSError, ValueError, RuntimeError):
            violations.append(value + ': registered review context unavailable')
    if context:
        binding['review_context'] = context
    fingerprint = _digest(binding)
    return dict(fingerprint=fingerprint, files=files, diff=''.join(diff_parts),
                violations=violations, truncated=truncated, reviewable=not violations,
                line_endings=line_endings, **({"review_context": context} if context else {}))
