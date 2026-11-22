"""Score independent client outcomes separately by lane and scan scope."""
import csv
import json
from collections import Counter

scores={}
def score(lane, broken, verdict):
    c=scores.setdefault(lane,Counter())
    outcome='break' if broken else 'control'
    if verdict=='UNCONFIRMED':
        outcome+='_abstained'
    elif verdict=='BREAKING':
        outcome+='_detected' if broken else '_false_alarm'
    elif broken:
        outcome+='_missed'
    else:
        outcome+='_accepted'
    c[outcome]+=1
with open('contracts.csv') as f:
    for row in csv.DictReader(f):
        score(row['lane'],int(row['client_exit'])!=0,row['checker'])
with open('visitor.csv') as f:
    for row in csv.DictReader(f):
        score('headers',int(row['client_exit'])!=0,row['checker'])
with open('provider-move.csv') as f:
    for row in csv.DictReader(f):
        score('single_provider_vs_package_oracle',int(row['client_exit'])!=0,row['single_provider_verdict'])
print(json.dumps(scores,indent=2))
