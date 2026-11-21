"""Create source-linked development candidates; never label generated items as gold."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from rahbar.config import load_retrieval_settings
from rahbar.retrieval.gemini import GeminiRouter


class Variant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Literal['ur','mixed','en']
    question: str
    history: list[dict[str,str]] = Field(default_factory=list)
    reference: str


class Scenario(BaseModel):
    model_config = ConfigDict(extra='forbid')
    variants: list[Variant] = Field(min_length=3,max_length=3)
    expected_outcome: Literal['answered','refused','clarification']
    evidence_groups: list[list[str]]
    reference_quotes: dict[str,str]


async def main(args):
    settings=load_retrieval_settings(args.project_root)
    records=[json.loads(line) for line in (args.corpus/'passages.jsonl').read_text().splitlines()]
    evidence={r['passage_id']:r['canonical_text'] for r in records}
    if len(records)>30:
        raise ValueError('Use an explicitly selected pilot source pack of at most 30 passages')
    args.output.mkdir(parents=True,exist_ok=True)
    async with httpx.AsyncClient() as client:
        router=GeminiRouter(client,settings.gemini_api_key,list(settings.models))
        categories=['single_topic']*4+['cross_topic']*2+['follow_up']*2+['uncovered','ambiguous']
        for index,category in enumerate(categories):
            target=args.output/f'family-{index:03}.json'
            if target.exists():
                continue
            response=await router.generate(
                'Draft one Auto Electrician tutor evaluation scenario in three equivalent language '
                'variants: ur (Urdu script only except unavoidable names), mixed (Urdu script with '
                'English technical terms), en (English). Use only the supplied passages for reference '
                'facts. Cite minimal REQUIRED evidence groups: each group lists alternative IDs '
                'supporting one required fact. Include exact reference_quotes for cited IDs. '
                'Cross_topic requires at least two facts across topics. Follow_up must include '
                'a prior question/answer in history and an elliptical current question. Uncovered '
                'requires refusal, ambiguous requires clarification, with empty evidence groups. '
                'Avoid actionable procedures, numerical specifications, and copying source QA wording. '
                'References for refusal/clarification describe the expected behavior. All material '
                'is unreviewed; do not assert human review.',
                {'scenario_number':index,'category':category,'passages':evidence},Scenario.model_json_schema(), max_output_tokens=8192)
            scenario=Scenario.model_validate(response.data)
            if {v.language for v in scenario.variants}!={'ur','mixed','en'}:
                raise ValueError('Missing language variant')
            ids={i for group in scenario.evidence_groups for i in group}
            if ids-set(evidence) or any(not g for g in scenario.evidence_groups):
                raise ValueError('Invalid evidence IDs')
            if scenario.expected_outcome=='answered' and not ids:
                raise ValueError('Answerable candidate lacks evidence')
            issues = []
            if any(not scenario.reference_quotes.get(i) or scenario.reference_quotes[i] not in evidence[i] for i in ids):
                issues.append('Reference quote does not exactly match source; reviewer correction required')
            result={'family_id':f'pilot-{index:03}','category':category,'review_status':'draft',
                    'reviewers':[],'review_issues':issues,'split':'development','generator_model':response.model,
                    'corpus_hash':hashlib.sha256((args.corpus/'passages.jsonl').read_bytes()).hexdigest(),
                    **scenario.model_dump()}
            target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            print(json.dumps({'saved':target.name,'model':response.model}),flush=True)
    rows=[]
    for path in sorted(args.output.glob('family-*.json')):
        family=json.loads(path.read_text())
        for variant in family.pop('variants'):
            rows.append(dict(family,**variant,id=family['family_id']+'-'+variant['language']))
    (args.output/'candidates.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('corpus',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--free-tier-confirmed',action='store_true',required=True)
    asyncio.run(main(parser.parse_args()))
