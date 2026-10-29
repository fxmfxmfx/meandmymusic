"""Public, bounded artist-batch summaries shared by the runner and manager."""
from uuid import UUID


def public_batch(value):
    if not isinstance(value, dict):
        return None
    result = {k: value[k] for k in ('total', 'completed', 'needs_review', 'waiting')
              if type(value.get(k)) is int and 0 <= value[k] <= 500}
    for key in ('artist', 'current'):
        if isinstance(value.get(key), str):
            result[key] = value[key][:300]
    result['items'] = []
    items = value.get('items', [])
    for item in items[:50] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            row = {'id': str(UUID(item.get('id')))}
        except (ValueError, TypeError, AttributeError):
            continue
        for key, limit in {'album': 300, 'phase': 40, 'status': 40, 'error': 1000}.items():
            if isinstance(item.get(key), str):
                row[key] = item[key][:limit]
        for key, fields in {'transfer': ('bytes', 'total_bytes', 'files', 'expected'), 'result': ('files',)}.items():
            if isinstance(item.get(key), dict):
                row[key] = {name: item[key][name] for name in fields
                            if type(item[key].get(name)) is int and item[key][name] >= 0}
        result['items'].append(row)
    return result
