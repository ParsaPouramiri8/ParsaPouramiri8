"""Standard-library-only SVG renderer and public GitHub stats collector."""
import argparse
import base64
import calendar
import json
import os
import sys
import urllib.request
from datetime import date, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent

def age(birth, today):
    if today < birth:
        raise ValueError('Birth date cannot be in the future')
    months = (today.year-birth.year)*12 + today.month-birth.month
    def anniversary(n):
        y, m = divmod(birth.year*12 + birth.month-1+n, 12)
        return date(y, m+1, min(birth.day, calendar.monthrange(y,m+1)[1]))
    if anniversary(months)>today:
        months -= 1
    return months//12, months%12, (today-anniversary(months)).days

def gql(query, variables):
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        raise RuntimeError('A GitHub token is required for --fetch. GitHub Actions provides it automatically.')
    req = urllib.request.Request('https://api.github.com/graphql',
        data=json.dumps({'query':query,'variables':variables}).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'parsa-profile'})
    with urllib.request.urlopen(req, timeout=60) as response:
        data=json.load(response)
    if data.get('errors'):
        raise RuntimeError('GitHub GraphQL: '+str(data['errors']))
    return data['data']

def collect(username):
    user=gql('''query($login:String!){user(login:$login){id followers{totalCount}}}''',{'login':username})['user']
    if not user:
        raise RuntimeError('GitHub user not found')
    repos=[]
    cursor=None
    while True:
        page=gql('''query($login:String!,$cursor:String){user(login:$login){repositories(first:100,after:$cursor,ownerAffiliations:[OWNER],privacy:PUBLIC){nodes{name isFork stargazerCount defaultBranchRef{target{oid}}} pageInfo{hasNextPage endCursor}}}}''',{'login':username,'cursor':cursor})['user']['repositories']
        repos.extend(page['nodes'])
        if not page['pageInfo']['hasNextPage']: break
        cursor=page['pageInfo']['endCursor']
    # Public, recent external contributions; pagination avoids truncated counts.
    contributed=set()
    cursor=None
    while True:
        page=gql('''query($login:String!,$cursor:String){user(login:$login){repositoriesContributedTo(first:100,after:$cursor,includeUserRepositories:false,privacy:PUBLIC,contributionTypes:[COMMIT,ISSUE,PULL_REQUEST,REPOSITORY]){nodes{nameWithOwner} pageInfo{hasNextPage endCursor}}}}''',{'login':username,'cursor':cursor})['user']['repositoriesContributedTo']
        contributed.update(r['nameWithOwner'] for r in page['nodes'])
        if not page['pageInfo']['hasNextPage']: break
        cursor=page['pageInfo']['endCursor']
    seen=set()
    added=deleted=commits=0
    for repo in repos:
        if repo['isFork'] or not repo['defaultBranchRef']: continue
        cursor=None
        # Pin each branch tip so a concurrent push cannot change pagination.
        tip=repo['defaultBranchRef']['target']['oid']
        while True:
            page=gql('''query($owner:String!,$name:String!,$tip:String!,$author:ID!,$cursor:String){repository(owner:$owner,name:$name){object(expression:$tip){... on Commit{history(first:100,after:$cursor,author:{id:$author}){nodes{oid additions deletions parents(first:2){totalCount}} pageInfo{hasNextPage endCursor}}}}}''',{'owner':username,'name':repo['name'],'tip':tip,'author':user['id'],'cursor':cursor})['repository']['object']['history']
            for commit in page['nodes']:
                if commit['oid'] in seen or commit['parents']['totalCount']>1: continue
                seen.add(commit['oid'])
                commits+=1
                added+=commit['additions']
                deleted+=commit['deletions']
            if not page['pageInfo']['hasNextPage']: break
            cursor=page['pageInfo']['endCursor']
    return {'repos':len(repos),'stars':sum(r['stargazerCount'] for r in repos if not r['isFork']),
        'followers':user['followers']['totalCount'],'contributed':len(contributed),
        'commits':commits,'additions':added,'deletions':deleted,'net_lines':added-deleted,
        'updated_at':datetime.now(ZoneInfo('UTC')).isoformat(),'complete':True}

def render(c,s,today):
    parts=['''<svg xmlns="http://www.w3.org/2000/svg" width="1815" height="855" viewBox="0 0 1815 855" role="img" aria-labelledby="title desc">
<title id="title">Parsa Pouramiri — developer profile</title>
<desc id="desc">Personal profile with age and public GitHub statistics. See README for counting scope.</desc>
<rect width="1815" height="855" rx="16" fill="#171c23"/>
<g transform="translate(535 0)">
<g font-family="DejaVu Sans Mono,Consolas,monospace" font-size="22">''']
    def text(x,y,value,color='#a9c9dd',size=22,extra=''):
        parts.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" {extra}>{escape(str(value))}</text>')
    def dots(x1,x2,y):
        if x2>x1: parts.append(f'<path d="M{x1} {y-3}H{x2}" stroke="#46505c" stroke-width="3" stroke-linecap="round" stroke-dasharray="1 12"/>')
    def row(label,value,y,size=22):
        # Keep the leaders out of both text runs; monospace sizing is deterministic.
        text(44,y,label+':','#ca946a',size)
        dots(44+(len(label)+1)*size*.603+16,1235-len(str(value))*size*.603-16,y)
        text(1235,y,value,size=size,extra='text-anchor="end"')
    def heading(label,y):
        text(20,y,'·','#626d78')
        text(44,y,label,'#c6ccd2',24)
        parts.append(f'<path d="M{64+len(label)*14.5} {y-6}H1235" stroke="#a0a9b2" stroke-width="1.4"/>')
    heading(c['name'],43)
    a=age(date.fromisoformat(c['birthdate']),today)
    rows=[('OS',c['os']),('Uptime',f'{a[0]} years, {a[1]} months, {a[2]} days'),('Host',c['host']),('Kernel',c['kernel']),('IDE',c['ide'])]
    for i,(label,value) in enumerate(rows): row(label,value,87+i*34)
    for i,(label,key) in enumerate([('Languages.Programming','programming'),('Languages.Computer','computer'),('Languages.Real','spoken')]): row(label,c[key],287+i*34)
    row('Hobbies.Software',c['software'],416)
    row('Hobbies.Hardware',c['hardware'],450,21)
    heading('Contact',509)
    row('Email.Personal',c['email'],549)
    row('LinkedIn',c['linkedin'],583)
    heading('GitHub Stats',647)
    def number(key):
        return f'{s[key]:,}' if s.get(key) is not None else '—'
    # Two aligned columns as in the reference, with a compact contributed count.
    def stat(label,value,x,y,right):
        text(x,y,label+':','#ca946a',21)
        dots(x+(len(label)+1)*12.66+14,right-len(value)*12.66-14,y)
        text(right,y,value,size=21,extra='text-anchor="end"')
    stat('Repos',number('repos'),44,687,290)
    text(312,687,'{Contributed: '+number('contributed')+'}', '#a9c9dd',21)
    text(782,687,'|','#7b8793')
    stat('Stars',number('stars'),815,687,1235)
    stat('Commits',number('commits'),44,723,751)
    text(782,723,'|','#7b8793')
    stat('Followers',number('followers'),815,723,1235)
    text(44,760,'Lines of Code on GitHub:','#ca946a',21)
    dots(355,545,760)
    text(570,760,number('net_lines'),size=21)
    text(785,760,'(', '#b9c3cd',21)
    text(810,760,number('additions')+'++','#78b77a',21)
    text(1040,760,number('deletions')+'--','#d4777c',21)
    text(1235,760,')','#b9c3cd',21,'text-anchor="end"')
    updated=s.get('updated_at','')
    note=('Stats updated: '+updated[:10]) if s.get('complete') else 'Stats pending first complete GitHub Actions run'
    text(44,808,note,'#788592',14)
    text(1235,808,'Public repos · counting scope in README','#788592',14,'text-anchor="end"')
    parts.append('</g></g>')
    portrait = ROOT / 'assets' / 'portrait.png'
    if portrait.exists():
        encoded = base64.b64encode(portrait.read_bytes()).decode('ascii')
        parts.append(f'<image x="24" y="95" width="500" height="665" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{encoded}"/>')
    parts.append('</svg>')
    (ROOT/'assets').mkdir(exist_ok=True)
    (ROOT/'assets/profile.svg').write_text('\n'.join(parts),encoding='utf-8')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args()
    config=json.loads((ROOT/'profile.json').read_text())
    path=ROOT/'stats.json'
    stats=json.loads(path.read_text()) if path.exists() else {}
    error=None
    if args.fetch:
        try:
            stats=collect(config['username'])
            tmp=path.with_suffix('.tmp')
            tmp.write_text(json.dumps(stats,indent=2)+'\n')
            tmp.replace(path)
        except Exception as exc:
            error=exc
    render(config,stats,datetime.now(ZoneInfo(config['timezone'])).date())
    if error:
        print('Stats refresh failed; previous statistics retained. '+str(error),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
