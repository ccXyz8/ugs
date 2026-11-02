"""Read-only pagination for public experiment views; never changes dispatch order."""
import re

PAGE_SIZE = 15
COMMANDS = {('experiment', 'list'), ('scheduler', 'status'), ('scheduler', 'get')}


def parse_page(args):
    supported = tuple(args[:2]) in COMMANDS
    clean = []
    page = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == '--page' or token.startswith('--page='):
            if not supported:
                raise ValueError('--page仅用于experiment list、scheduler status/get')
            if page is not None:
                raise ValueError('--page不能重复指定')
            if token == '--page':
                i += 1
                if i >= len(args):
                    raise ValueError('--page需要从1开始的整数页码')
                value = args[i]
            else:
                value = token.split('=', 1)[1]
            if not re.fullmatch(r'[0-9]+', value) or int(value) < 1:
                raise ValueError('--page需要从1开始的整数页码')
            page = int(value)
        else:
            clean.append(token)
        i += 1
    return clean, (page or 1) if supported else None


def paginate(data, args, page):
    if page is None:
        return data, None
    is_list = tuple(args[:2]) == ('experiment', 'list')
    rows = data if is_list else data['jobs']
    # Immutable registration ID, not mutable queue priority or execution status.
    ordered = sorted(rows, key=lambda row: int(row['job_id'].split('-')[-1]), reverse=True)
    total = len(ordered)
    offset = (page - 1) * PAGE_SIZE
    selected = ordered[offset:offset + PAGE_SIZE]
    info = {'page': page, 'page_size': PAGE_SIZE, 'total_jobs': total,
            'total_pages': max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            'shown': len(selected)}
    return (selected if is_list else {**data, 'jobs': selected}), info


def footer(info):
    return ('第 {page} 页 / 共 {total_pages} 页；本页 {shown} 个 / 共 {total_jobs} 个实验；'
            '每页 {page_size} 个，最新在前。使用 --page N 翻页。').format(**info)
