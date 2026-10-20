const fieldsFor = flag => flag === "read_later" ? ["read_later_at", "read_later_done_at"] : [`${flag}_at`];

// Batch read writes across articles without delaying their optimistic UI state.
// ItemStateQueue still serializes a later unread toggle behind that item's save.
export class ReadBatchWriter {
  constructor(save) {
    this.save = save;
    this.pending = new Map();
    this.running = false;
    this.scheduled = false;
  }

  add(id) {
    const done = new Promise((resolve, reject) => {
      if (!this.pending.has(id)) this.pending.set(id, []);
      this.pending.get(id).push({ resolve, reject });
    });
    if (!this.running && !this.scheduled) {
      this.scheduled = true;
      queueMicrotask(() => this.flush());
    }
    return done;
  }

  async flush() {
    this.scheduled = false;
    this.running = true;
    while (this.pending.size) {
      const batch = this.pending;
      this.pending = new Map();
      try {
        await this.save([...batch.keys()]);
        const result = { read_at: new Date().toISOString() };
        for (const waiters of batch.values()) for (const waiter of waiters) waiter.resolve(result);
      } catch (error) {
        for (const waiters of batch.values()) for (const waiter of waiters) waiter.reject(error);
      }
    }
    this.running = false;
  }
}

// Keep clicks responsive while serializing writes to each article. A newer click
// remains visible even when an older request succeeds or fails out of date.
export class ItemStateQueue {
  constructor({ save, onChange, onSaved, onError }) {
    Object.assign(this, { save, onChange, onSaved, onError });
    this.pending = new Map();
    this.changes = new Map();
    this.version = 0;
  }

  has(id, flag) { return this.pending.get(id)?.desired.has(flag) || false; }
  hasAny(flag) { return [...this.pending.values()].some(queue => queue.desired.has(flag)); }

  settle() {
    return Promise.all([...this.pending.values()].map(queue =>
      new Promise(resolve => queue.waiters.push({ resolve }))));
  }

  // Responses that began before a click (or while its save was pending) must
  // not replace the user's newer state. Later reads can still sync other devices.
  reconcile(id, data, version) {
    for (const [field, change] of this.changes.get(id) || []) {
      if (change.version > version || (field.endsWith("_at") && this.has(id, change.flag))) data[field] = change.value;
    }
    return data;
  }

  publish(id, flag, patch) {
    const version = ++this.version;
    if (!this.changes.has(id)) this.changes.set(id, new Map());
    for (const [field, value] of Object.entries(patch))
      this.changes.get(id).set(field, { value, version, flag });
    this.onChange(id, patch);
  }

  set(item, flag, value, silent = false, complete = false) {
    const id = item.id;
    let queue = this.pending.get(id);
    if (!queue) {
      queue = { desired: new Map(), confirmed: {}, running: false, waiters: [] };
      this.pending.set(id, queue);
    }
    if (!queue.desired.has(flag)) {
      for (const field of fieldsFor(flag)) queue.confirmed[field] = item[field] ?? null;
    }
    const timestamp = new Date().toISOString();
    const patch = { [`${flag}_at`]: value ? timestamp : null };
    if (flag === "read_later") patch.read_later_done_at = !value && complete ? timestamp : null;
    queue.desired.set(flag, { value, silent, complete, patch });
    const done = new Promise(resolve => queue.waiters.push({ resolve, flag, patch }));
    this.publish(id, flag, patch);
    if (!queue.running) void this.flush(id, queue);
    return done;
  }

  async flush(id, queue) {
    queue.running = true;
    while (queue.desired.size) {
      const sent = new Map(queue.desired);
      let data, error;
      try {
        const body = Object.fromEntries([...sent].map(([flag, intent]) => [flag, intent.value]));
        if (sent.get("read_later")?.complete) body.read_later_done = true;
        data = await this.save(id, body);
        data = data.state || data;
      } catch (failure) { error = failure; }
      const saved = {};
      for (const [flag, intent] of sent) {
        const fields = fieldsFor(flag);
        if (!error) {
          for (const field of fields)
            queue.confirmed[field] = Object.hasOwn(data, field) ? data[field] : intent.patch[field];
        }
        if (queue.desired.get(flag) !== intent) continue;
        queue.desired.delete(flag);
        const patch = Object.fromEntries(fields.map(field => [field, queue.confirmed[field]]));
        if (!error && flag === "read_later") {
          for (const field of ["detail_status", "ai_status", "detail_ready"])
            if (Object.hasOwn(data, field)) patch[field] = data[field];
        }
        this.publish(id, flag, patch);
        if (!error) saved[flag] = intent;
      }
      if (error) this.onError(error);
      else if (Object.keys(saved).length) this.onSaved(id, saved);
    }
    this.pending.delete(id);
    for (const { resolve, flag, patch } of queue.waiters)
      resolve(!flag || fieldsFor(flag).every(field => !!queue.confirmed[field] === !!patch[field]));
  }
}
