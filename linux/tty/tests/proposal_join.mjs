// Offline integration worker: actual repo modules, explicitly fake model answers.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const [opsRoot, uiRoot, catalogPath, selectedId] = process.argv.slice(2);
const load = path => import(pathToFileURL(path).href);
const { compareContext } = await load(resolve(opsRoot, 'packages/semcmp/context.mjs'));
const { JEV_MODEL } = await load(resolve(opsRoot, 'packages/jev-review/core.mjs'));
const { detailOf } = await load(resolve(uiRoot, 'capabilities/proposal-detail/src/index.mjs'));
globalThis.fetch = () => { throw new Error('NETWORK_FORBIDDEN'); };
const editor = JSON.parse(readFileSync(0, 'utf8'));
const query = {
  input: editor.lines.slice(editor.span[0] - 1, editor.span[1]).join('\n'),
  state: { currentRef: 'synthetic-poc-v1', editor },
  focus: { target: editor.target, revision: editor.revision, cursor: editor.cursor, span: editor.span },
};
const envelope = await compareContext(JSON.stringify(query), readFileSync(catalogPath, 'utf8'), {
  ask: async (state, questions) => {
    assert.deepEqual(state.query, query);
    assert.ok(state.proposals.every(row => !Object.hasOwn(row, 'detail')));
    return { model: JEV_MODEL, answers: Object.fromEntries(Object.keys(questions).map((key, index) =>
      [key, { type: 'noul', noul: state.proposals[index].id === 'B' ? 0.9 : 0.1 }])), usage: {} };
  },
});
const selection = { requestId: envelope.requestId, id: selectedId };
const detail = await detailOf(envelope, selection);
const proposal = envelope.proposals[detail.index];
assert.equal(proposal.id, selectedId);
assert.equal(detail.requestId, envelope.requestId);
const view = {
  requestId: envelope.requestId, selectedId,
  text: query.input, candidates: envelope.proposals.map(row => row.representation),
  selectedIndex: detail.index, meaning: JSON.stringify(proposal.meaning),
  notes: envelope.proposals.map(row => JSON.stringify(row.evidence)), detail,
};
process.stdout.write(JSON.stringify({ query: envelope.query, selection, proposal, view }));
