"""Download original real datasets. Uses only Python standard library.
Run: python download_sources.py --dataset nlr
     python download_sources.py --dataset m100 --list-only
     python download_sources.py --dataset m100 --max-files 1
     python download_sources.py --dataset all
Large files may require substantial disk space and time.
"""
import argparse, json, pathlib, urllib.request
from urllib.parse import quote

DEST = pathlib.Path(__file__).parent / 'original_downloads'
ZENODO = {'m100': '7541722', 'hazardnet': '10050368'}
NLR_URLS = {
    'esif_power_cooling.csv.zip': 'https://data.nlr.gov/system/files/300/1757105566-esif.influx.buildingData.PUE.combined.csv.zip',
    'esif_weather.parquet': 'https://data.nlr.gov/system/files/300/1757105566-esif.influx.buildingData.outside.combined_2.parquet',
}

def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'AO02-research-data/1.0'}), timeout=60) as r:
        return json.load(r)

def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        print('Already exists:', path)
        return
    print('Downloading:', path.name, 'from', url, flush=True)
    try:
        urllib.request.urlretrieve(url, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise

def zenodo(dataset, list_only, max_files):
    record = get_json(f'https://zenodo.org/api/records/{ZENODO[dataset]}')
    files = record.get('files', [])
    print(f'{dataset}: {len(files)} source files')
    for f in files:
        print(f"  {f.get('key')} ({f.get('size',0)/1e6:.1f} MB)")
    if list_only:
        return
    for f in files[:max_files] if max_files is not None else files:
        link = f.get('links',{}).get('self')
        if not link:
            print('No direct download link:', f.get('key'))
            continue
        path = DEST / dataset / pathlib.Path(f['key']).name
        download(link, path)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--dataset', choices=['m100','hazardnet','nlr','all'], default='nlr')
    p.add_argument('--list-only', action='store_true')
    p.add_argument('--max-files', type=int, default=None)
    args=p.parse_args()
    targets=['m100','hazardnet','nlr'] if args.dataset=='all' else [args.dataset]
    for name in targets:
        if name in ZENODO:
            zenodo(name,args.list_only,args.max_files)
        else:
            for filename,url in NLR_URLS.items():
                print(filename,url)
                if not args.list_only:
                    download(url,DEST/'nlr'/filename)
if __name__=='__main__':
    main()
