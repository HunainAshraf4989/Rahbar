"""Export the opt-in text API wire contracts without loading models or credentials."""
import json
from pathlib import Path
from typing import cast

from rahbar.retrieval.answer import AnswerService
from rahbar.retrieval.api import TextQuestion, TurnAnswer, create_text_app
from rahbar.retrieval.sessions import TextSessions

root=Path(__file__).resolve().parents[2]
app=create_text_app(cast(AnswerService,None),cast(TextSessions,None),'schema-only')
(root/'contracts/rag.openapi.json').write_text(json.dumps(app.openapi(),indent=2,ensure_ascii=False)+'\n')
schema={'title':'TextRagContracts','anyOf':[TextQuestion.model_json_schema(),TurnAnswer.model_json_schema()]}
# Hoist shared definitions so JSON Schema references resolve from the document root.
definitions={}
for item in schema['anyOf']:
    definitions.update(item.pop('$defs',{}))
schema['$defs']=definitions
(root/'contracts/rag.schema.json').write_text(json.dumps(schema,indent=2,ensure_ascii=False)+'\n')
