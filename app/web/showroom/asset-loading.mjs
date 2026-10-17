export function createTaskQueue(limit, state = {}) {
  if (!Number.isInteger(limit) || limit < 1) throw new RangeError('Invalid queue limit');
  Object.assign(state, { limit, queued: 0, active: 0, peak: 0, completed: 0, failed: 0 });
  const pending = [];
  function pump() {
    while (state.active < limit && pending.length) {
      const item = pending.shift();
      state.queued--; state.active++; state.peak = Math.max(state.peak, state.active);
      Promise.resolve().then(item.task).then(value => {
        state.completed++; item.resolve(value);
      }, error => {
        state.failed++; item.reject(error);
      }).finally(() => {
        state.active--; pump();
      });
    }
  }
  return task => new Promise((resolve, reject) => {
    pending.push({ task, resolve, reject }); state.queued++; pump();
  });
}

export async function settleModels(modelPromises, releaseDecoder) {
  try {
    const results = await Promise.allSettled(modelPromises);
    const failed = results.find(result => result.status === 'rejected');
    if (failed) throw failed.reason;
    return results.map(result => result.value);
  } finally {
    await releaseDecoder();
  }
}
