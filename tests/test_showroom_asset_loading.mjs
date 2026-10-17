import assert from 'node:assert/strict';
import { createTaskQueue, settleModels } from '../app/web/showroom/asset-loading.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
let assertions = 0;
const check = (value, expected, message) => { assert.deepEqual(value, expected, message); assertions++; };

const state = {}, queue = createTaskQueue(2, state), first = deferred(), second = deferred();
const started = [];
const jobs = [
  queue(() => { started.push('first'); return first.promise; }),
  queue(() => { started.push('second'); return second.promise; }),
  queue(() => { started.push('throws'); throw new Error('sync failure'); }),
  queue(() => { started.push('rejects'); return Promise.reject(new Error('async failure')); }),
  queue(() => { started.push('last'); return 'last result'; })
];
const finished = Promise.allSettled(jobs);
await tick();
check(started, ['first', 'second'], 'Only two tasks may enter');
check([state.active, state.queued, state.peak], [2, 3, 2], 'Two occupied slots');
const unrelated = createTaskQueue(1, {});
check(await unrelated(() => 'independent'), 'independent', 'Another queue must remain independent');
first.reject(new Error('first failure')); second.resolve('second result');
const results = await finished; await tick();
check(results.map(result => result.status), ['rejected', 'fulfilled', 'rejected', 'rejected', 'fulfilled'], 'Failures do not deadlock later work');
check(results[4].value, 'last result', 'Final queued work completed');
check([state.active, state.queued, state.peak, state.completed, state.failed], [0, 0, 2, 2, 3], 'All slots were released');

const parserA = deferred(), parserB = deferred(), decoderTask = deferred();
const expectedError = new Error('model parse failure');
let cleanupCalls = 0, modelResultSettled = false;
const models = settleModels([parserA.promise, parserB.promise], async () => {
  cleanupCalls++; await decoderTask.promise;
});
const outcome = models.then(value => ({ value }), error => ({ error })).finally(() => { modelResultSettled = true; });
parserA.reject(expectedError); await tick();
check(cleanupCalls, 0, 'Do not dispose while another parser is pending');
parserB.resolve('trees'); await tick();
check([cleanupCalls, modelResultSettled], [1, false], 'Pending decoder tasks delay completion');
decoderTask.resolve();
check((await outcome).error, expectedError, 'Return the original parse error after cleanup');
let successCleanup = 0;
check(await settleModels([Promise.resolve('city'), Promise.resolve('trees')], () => { successCleanup++; }), ['city', 'trees'], 'Successful model order preserved');
check(successCleanup, 1, 'Successful cleanup occurs exactly once');

console.log(JSON.stringify({ passed: true, assertions, checks: ['image concurrency cap', 'failure releases slots', 'independent queues', 'all parsers settle before cleanup', 'async cleanup awaited', 'success cleanup once'] }));
