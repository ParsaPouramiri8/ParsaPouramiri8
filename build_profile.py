"""Standard-library-only SVG renderer and public GitHub stats collector."""
import argparse
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
    # Reference-style compact neofetch card: real monospace text throughout.
    bg='#161b22'; fg='#afd7ff'; orange='#ffaf5f'; muted='#5f87af'
    parts=['''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="465" viewBox="0 0 1000 465" role="img" aria-labelledby="title desc">
<title id="title">Parsa Pouramiri</title>
<desc id="desc">ASCII head portrait and developer profile. Public GitHub statistics; scope explained in README.</desc>
<rect width="1000" height="465" rx="15" fill="#161b22"/>
<g font-family="'Liberation Mono','Courier New',monospace" font-size="14.5" font-weight="400">''']
    def text(x,y,value,color=fg,size=14.5,extra=''):
        parts.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" {extra}>{escape(str(value))}</text>')
    def leader(x1,x2,y):
        # Actual periods match the terminal reference more closely than dashed lines.
        count=int((x2-x1)/8.7)
        if count>0: text(x1,y,'.'*count,muted)
    left=350; right=983
    def row(label,value,y):
        text(left-16,y,'.',muted)
        text(left,y,label+':',orange)
        value=str(value)
        size=14.5
        # Long user-entered contacts fit without colliding with their label.
        available=right-left-(len(label)+1)*8.7-17.4
        size=min(size,available/(max(1,len(value))*.6))
        leader(left+(len(label)+1)*8.7+8.7,right-len(value)*size*.6-8.7,y)
        text(right,y,value,size=size,extra='text-anchor="end"')
    def heading(label,y,first=False):
        x=left-16
        text(x,y,('' if first else '- ')+label)
        start=x+(len(label)+(0 if first else 2))*8.7+10
        parts.append(f'<path d="M{start} {y-4}H{right}" stroke="#b9c6d2" stroke-width="0.7"/>')
    heading(c['name'],27,True)
    a=age(date.fromisoformat(c['birthdate']),today)
    values=[('OS',c['os']),('Uptime',f'{a[0]} years, {a[1]} months, {a[2]} days'),('Host',c['host']),('Kernel',c['kernel']),('IDE',c['ide'])]
    for i,(label,value) in enumerate(values): row(label,value,47+i*18)
    for i,(label,key) in enumerate([('Languages.Programming','programming'),('Languages.Computer','computer'),('Languages.Real','spoken')]): row(label,c[key],155+i*18)
    row('Hobbies.Software',c['software'],227)
    row('Hobbies.Hardware',c['hardware'],245)
    heading('Contact',281)
    row('Email.Personal',c['email'],299)
    row('LinkedIn',c['linkedin'],317)
    heading('GitHub Stats',371)
    def num(key): return f'{s[key]:,}' if s.get(key) is not None else '—'
    def stat(label,value,x,y,end):
        text(x,y,label+':',orange)
        leader(x+(len(label)+1)*8.7+8.7,end-len(value)*8.7-8.7,y)
        text(end,y,value,extra='text-anchor="end"')
    text(left-16,389,'.',muted)
    text(left,389,'Repos:',orange)
    text(left+86,389,num('repos'))
    cx=left+126
    text(cx,389,'{Contributed:',orange)
    text(cx+122,389,num('contributed')+'}')
    text(752,389,'|')
    stat('Stars',num('stars'),770,389,right)
    text(left-16,407,'.',muted)
    stat('Commits',num('commits'),left,407,733)
    text(752,407,'|')
    stat('Followers',num('followers'),770,407,right)
    text(left-16,425,'.',muted)
    # Text runs pack the last line with the reference's green/red counters.
    label='Lines of Code on GitHub: '
    runs=[(label,orange),(num('net_lines')+' ( ',fg),(num('additions')+'++', '#87d787'),(', ',fg),(num('deletions')+'--','#ff5f5f'),(' )',fg)]
    total=sum(len(v) for v,col in runs)
    size=min(14.5,(right-left)/(total*.6))
    x=left
    for value,color in runs:
        text(x,425,value,color,size)
        x+=len(value)*size*.6
    portrait=(ROOT/'assets/portrait.txt').read_text(encoding='utf-8').splitlines()
    parts.append('<g transform="translate(4 40) scale(1.10 0.85)">')
    for i,line in enumerate(portrait):
        text(8,28+i*13.6,line,'#bfdcff',15.2,'xml:space="preserve"')
    parts.append('</g>')
    parts.append('</g></svg>')
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
