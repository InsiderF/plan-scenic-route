#!/usr/bin/env python3
"""Publish a selected static map using an already installed Wrangler CLI."""
import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--wrangler', default='wrangler')
    p.add_argument('--authorized', action='store_true')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if not a.authorized:
        p.error('需先明确本次地图的发布项目和用户授权')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,57}[a-z0-9]|[a-z0-9]', a.project):
        p.error('项目名无效')
    folder = a.directory.resolve()
    if not (folder/'index.html').is_file():
        p.error('发布目录缺少index.html')
    if any(f.is_symlink() or (f.is_file() and f.suffix.lower() not in {'.html','.png','.svg','.webp','.ico','.mp3','.opus','.ogg','.m4a'}) for f in folder.rglob('*')):
        p.error('移出来源配置、凭据与临时文件；只发布网页和图片')
    exe = shutil.which(a.wrangler)
    if not exe:
        p.error('未找到Wrangler；按hosting.md完成首次配置，先交付已生成文件')
    try:
        result = subprocess.run([exe,'pages','deploy',str(folder),'--project-name',a.project], text=True,capture_output=True,timeout=180)
        if result.returncode:
            raise RuntimeError('托管发布失败，请在本地工具检查账号授权和项目。')
        links = re.findall(r'https://[a-zA-Z0-9.-]+\.pages\.dev',result.stdout+result.stderr)
        if not links:
            raise RuntimeError('未返回可验证的HTTPS网址')
        url = links[-1]
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'plan-scenic-route/1.0 publication-check'}),timeout=30) as response:
            if response.status != 200 or b'id="route-data"' not in response.read():
                raise RuntimeError('发布网址未返回地图网页')
        out = {'status':'published','url':url,'verified_at_host':True,'android_mainland_network':'not_verified',
               'note':'服务器访问成功不代表国内安卓实测通过。'}
        a.out.parent.mkdir(parents=True,exist_ok=True)
        a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(out,ensure_ascii=False))
        return 0
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
        a.out.unlink(missing_ok=True)
        print(json.dumps({'status':'failed','reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
