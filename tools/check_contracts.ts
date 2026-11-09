import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import Ajv2020 from 'ajv/dist/2020.js';
import type { SentenceApprovedEvent } from '../app/web/src/api/contracts.generated.js';

const schema = JSON.parse(fs.readFileSync('contracts/contracts.schema.json', 'utf8'));
const catalog = JSON.parse(fs.readFileSync('contracts/events.json', 'utf8'));
const ajv = new Ajv2020({strict: false, allErrors: true});
ajv.addFormat("password", true);
ajv.addSchema(schema, 'rahbar');
let count = 0;
for (const file of fs.readdirSync('contracts/fixtures').sort()) {
  for (const record of JSON.parse(fs.readFileSync(path.join('contracts/fixtures',file),'utf8'))) {
    const model = record.model === 'event' ? catalog[record.direction][record.data.type] : record.model;
    const validate = model ? ajv.compile({$ref:`rahbar#/$defs/${model}`}) : null;
    const valid = validate ? validate(record.data) : false;
    assert.equal(valid, record.valid !== false, `${file}: ${JSON.stringify(validate?.errors)}`);
    if (valid) assert.deepEqual(JSON.parse(JSON.stringify(record.data)), record.data);
    count++;
  }
}
const approved: SentenceApprovedEvent = JSON.parse(fs.readFileSync('contracts/fixtures/normal_answer.json','utf8'))[0].data;
assert.equal(approved.payload.canonical_text, 'یہ relay کی coil ہے۔');
assert.equal(approved.payload.visual_target, null);
console.log(`${count} fixtures validated and round-tripped in TypeScript`);
