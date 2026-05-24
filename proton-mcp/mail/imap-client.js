/**
 * IMAP client for Proton Bridge
 * Handles read operations: list, get, search, unread, thread, mark
 */

import Imap from 'imap';
import { simpleParser } from 'mailparser';

/**
 * Create a short-lived IMAP connection, run `fn`, then close.
 */
function withImap(config, fn) {
  return new Promise((resolve, reject) => {
    const imap = new Imap({
      user: config.username,
      password: config.password,
      host: config.imap_host || '127.0.0.1',
      port: config.imap_port || 1143,
      tls: false,
      authTimeout: 10000,
    });

    imap.once('ready', () => {
      fn(imap)
        .then((result) => {
          imap.end();
          resolve(result);
        })
        .catch((err) => {
          imap.end();
          reject(err);
        });
    });

    imap.once('error', (err) => reject(new Error(`IMAP connection failed: ${err.message}. Is Proton Bridge running?`)));
    imap.connect();
  });
}

/**
 * Fetch messages by sequence numbers. Returns parsed message objects.
 */
function fetchMessages(imap, seqnos, bodiesOpt = '') {
  return new Promise((resolve, reject) => {
    if (seqnos.length === 0) { resolve([]); return; }

    const messages = [];
    const f = imap.fetch(seqnos, { bodies: bodiesOpt, struct: true });

    f.on('message', (msg, seqno) => {
      let raw = '';
      msg.on('body', (stream) => {
        stream.on('data', (chunk) => { raw += chunk.toString('utf8'); });
      });
      msg.once('end', () => {
        messages.push({ seqno, raw });
      });
    });

    f.once('error', reject);
    f.once('end', async () => {
      const parsed = [];
      for (const m of messages) {
        try {
          const mail = await simpleParser(m.raw);
          parsed.push({
            id: m.seqno,
            subject: mail.subject || '(no subject)',
            from: mail.from?.text || 'unknown',
            to: mail.to?.text || '',
            cc: mail.cc?.text || '',
            date: mail.date?.toISOString() || '',
            body: mail.text || mail.html || '',
            message_id_header: mail.messageId || null,
            in_reply_to: mail.inReplyTo || null,
            references: mail.references || [],
            seen: true, // will be overridden by caller if needed
          });
        } catch {
          parsed.push({ id: m.seqno, subject: '(parse error)', from: '', to: '', date: '', body: '', message_id_header: null, in_reply_to: null, references: [], seen: true });
        }
      }
      resolve(parsed);
    });
  });
}

/**
 * Open INBOX and return box info.
 */
function openInbox(imap, readOnly = true) {
  return new Promise((resolve, reject) => {
    imap.openBox('INBOX', readOnly, (err, box) => {
      if (err) reject(err);
      else resolve(box);
    });
  });
}

function openFolder(imap, folder, readOnly = true) {
  return new Promise((resolve, reject) => {
    imap.openBox(folder, readOnly, (err, box) => {
      if (err) reject(err);
      else resolve(box);
    });
  });
}

/**
 * IMAP SEARCH wrapper.
 */
function imapSearch(imap, criteria) {
  return new Promise((resolve, reject) => {
    imap.search(criteria, (err, results) => {
      if (err) reject(err);
      else resolve(results || []);
    });
  });
}

// --- Exported operations ---

export async function getUnread(config) {
  return withImap(config, async (imap) => {
    await openInbox(imap, true);
    const uids = await imapSearch(imap, ['UNSEEN']);
    if (uids.length === 0) return { count: 0, messages: [] };

    const recent = uids.slice(-20);
    const messages = await fetchMessages(imap, recent, 'HEADER');
    return {
      count: uids.length,
      messages: messages.map((m) => ({
        id: m.id,
        subject: m.subject,
        from: m.from,
        date: m.date,
      })),
    };
  });
}

export async function listMessages(config, limit = 10) {
  return withImap(config, async (imap) => {
    const box = await openInbox(imap, true);
    const total = box.messages.total;
    if (total === 0) return [];

    const start = Math.max(1, total - limit + 1);
    const range = `${start}:${total}`;

    const messages = await fetchMessages(imap, range, 'HEADER');

    // Check which are unseen
    const unseenIds = new Set(await imapSearch(imap, ['UNSEEN']));
    return messages
      .map((m) => ({ ...m, seen: !unseenIds.has(m.id), body: undefined }))
      .reverse();
  });
}

export async function getMessage(config, messageId, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, true);
    const messages = await fetchMessages(imap, [messageId], '');
    if (messages.length === 0) throw new Error(`Message ${messageId} not found in ${folder}`);
    return { ...messages[0], folder };
  });
}

export async function searchMessages(config, query) {
  return withImap(config, async (imap) => {
    const allMessages = [];
    const folders = ['INBOX', 'Sent', 'Drafts', 'Archive'];

    for (const folder of folders) {
      try {
        await openFolder(imap, folder, true);
        const uids = await imapSearch(imap, [['TEXT', query]]);
        if (uids.length > 0) {
          const recent = uids.slice(-10);
          const messages = await fetchMessages(imap, recent, 'HEADER');
          allMessages.push(...messages.map((m) => ({ ...m, body: undefined, folder })));
        }
      } catch {
        // Folder may not exist — skip silently
      }
    }

    // Sort by date descending, return up to 20 results
    allMessages.sort((a, b) => new Date(b.date) - new Date(a.date));
    return allMessages.slice(0, 20);
  });
}

export async function getMessageHeaders(config, messageId, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, true);
    const messages = await fetchMessages(imap, [messageId], 'HEADER');
    if (messages.length === 0) throw new Error(`Message ${messageId} not found in ${folder}`);
    return {
      messageId: messages[0].message_id_header,
      subject: messages[0].subject,
      from: messages[0].from,
      to: messages[0].to,
      cc: messages[0].cc || null,
      references: messages[0].references,
      inReplyTo: messages[0].in_reply_to,
    };
  });
}

// Build a nested IMAP OR criterion over many Message-ID searches:
//   ['OR', ['HEADER','Message-ID',a], ['OR', ['HEADER','Message-ID',b], ...]]
// One search lets Proton Bridge do a single decrypt pass instead of N.
function buildMessageIdOr(refIds) {
  if (refIds.length === 0) return null;
  if (refIds.length === 1) return ['HEADER', 'Message-ID', refIds[0]];
  return ['OR', ['HEADER', 'Message-ID', refIds[0]], buildMessageIdOr(refIds.slice(1))];
}

// Proton Bridge synthesizes per-message threading IDs like
//   <abc...==@protonmail.internalid>
// These are NOT real Message-IDs — searching for them via IMAP HEADER is both
// useless (they only ever exist on one message) and expensive (Proton Bridge
// has to decrypt every message in the folder to evaluate the search).
function isSyntheticRef(id) {
  return typeof id === 'string' && id.includes('@protonmail.internalid');
}

export async function getThread(config, messageId, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, true);

    // 1. Headers only — we don't need the body to read threading info.
    const startMsgs = await fetchMessages(imap, [messageId], 'HEADER');
    if (startMsgs.length === 0) throw new Error(`Message ${messageId} not found in ${folder}`);
    const start = startMsgs[0];

    // 2. Normalize references — mailparser returns a string for a single ref,
    //    an array for multiple. Spreading a string would explode it into chars
    //    and detonate the timeout.
    const rawRefs = start.references;
    const referencesArr = Array.isArray(rawRefs)
      ? rawRefs
      : (rawRefs ? [rawRefs] : []);

    // 3. Real Message-IDs only. Synthetic protonmail.internalid refs are
    //    dropped — they cost a full decrypt pass to search and never match.
    const realRefs = referencesArr.filter((r) => !isSyntheticRef(r));
    const inReplyTo = isSyntheticRef(start.in_reply_to) ? null : start.in_reply_to;

    // 4. Fetch the body of the seed message — we will return at least this.
    const seedFull = await fetchMessages(imap, [messageId], '');
    const collected = seedFull.map((m) => ({ ...m, folder }));

    // 5. If there are no real ancestor references and no In-Reply-To, this is
    //    a standalone message (e.g. a Glassdoor notification). There's no
    //    thread to find — return the seed without paying for a cross-folder
    //    HEADER scan.
    const hasAncestors = realRefs.length > 0 || !!inReplyTo;
    if (!hasAncestors) {
      return collected;
    }

    // 6. Build search criterion over real Message-IDs only.
    const refIds = [start.message_id_header, ...realRefs, inReplyTo]
      .filter(Boolean)
      .filter((r) => !isSyntheticRef(r));
    const orCriterion = buildMessageIdOr(refIds);
    if (!orCriterion) return collected;

    // 7. Search across the folders that typically hold a conversation. Skip
    //    "All Mail" — it's a superset of the others and doubles the cost.
    const candidateFolders = ['INBOX', 'Sent', 'Archive'];
    for (const f of candidateFolders) {
      try {
        await openFolder(imap, f, true);
        const seqnos = await imapSearch(imap, [orCriterion]);
        // In the seed's own folder, skip the seed itself (already collected).
        const filtered = (f === folder) ? seqnos.filter((n) => n !== messageId) : seqnos;
        if (filtered.length === 0) continue;
        const msgs = await fetchMessages(imap, filtered, '');
        collected.push(...msgs.map((m) => ({ ...m, folder: f })));
      } catch {
        // Folder may not exist or search may fail — skip silently.
      }
    }

    // 8. Deduplicate by Message-ID header (same message can appear in multiple
    //    folders, e.g. INBOX + Archive).
    const seen = new Set();
    const unique = [];
    for (const m of collected) {
      const key = m.message_id_header || `${m.folder}:${m.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(m);
    }

    // 9. Sort chronologically.
    return unique.sort((a, b) => new Date(a.date) - new Date(b.date));
  });
}

export async function markMessage(config, messageId, read, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, false);
    await new Promise((resolve, reject) => {
      const fn = read ? imap.addFlags.bind(imap) : imap.delFlags.bind(imap);
      fn(messageId, ['\\Seen'], (err) => {
        if (err) reject(err); else resolve();
      });
    });
    return { success: true, message_id: messageId, folder, read };
  });
}

export async function starMessage(config, messageId, star, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, false);
    await new Promise((resolve, reject) => {
      const fn = star ? imap.addFlags.bind(imap) : imap.delFlags.bind(imap);
      fn(messageId, ['\\Flagged'], (err) => {
        if (err) reject(err); else resolve();
      });
    });
    return { success: true, message_id: messageId, folder, starred: star };
  });
}

export async function deleteMessage(config, messageId, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, false);
    // node-imap exposes addFlags/delFlags, not store(). Using `store()` (as the
    // previous code did) throws "imap.store is not a function".
    await new Promise((resolve, reject) => {
      imap.addFlags(messageId, ['\\Deleted'], (err) => {
        if (err) reject(err); else resolve();
      });
    });
    await new Promise((resolve, reject) => {
      imap.expunge((err) => {
        if (err) reject(err); else resolve();
      });
    });
    return { success: true, message_id: messageId, folder, deleted: true };
  });
}

export async function moveMessage(config, messageId, destFolder, sourceFolder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, sourceFolder, false);
    await new Promise((resolve, reject) => {
      imap.move(messageId, destFolder, (err) => {
        if (err) reject(err); else resolve();
      });
    });
    return { success: true, message_id: messageId, moved_from: sourceFolder, moved_to: destFolder };
  });
}

export async function listFolders(config) {
  return withImap(config, async (imap) => {
    return new Promise((resolve, reject) => {
      imap.getBoxes((err, boxes) => {
        if (err) reject(err);
        else {
          const folders = [];
          function walk(obj, prefix = '') {
            for (const [name, box] of Object.entries(obj)) {
              const fullName = prefix ? `${prefix}${box.delimiter}${name}` : name;
              folders.push({ name: fullName, delimiter: box.delimiter });
              if (box.children) walk(box.children, fullName);
            }
          }
          walk(boxes);
          resolve(folders);
        }
      });
    });
  });
}

function openBox(imap, folder, readOnly = true) {
  return new Promise((resolve, reject) => {
    imap.openBox(folder, readOnly, (err, box) => {
      if (err) reject(err);
      else resolve(box);
    });
  });
}

export async function listMessagesInFolder(config, folder, limit = 10) {
  return withImap(config, async (imap) => {
    const box = await openBox(imap, folder, true);
    const total = box.messages.total;
    if (total === 0) return [];

    const start = Math.max(1, total - limit + 1);
    const range = `${start}:${total}`;

    const messages = await fetchMessages(imap, range, 'HEADER');
    return messages
      .map((m) => ({ ...m, body: undefined, folder }))
      .reverse();
  });
}

export async function getAttachments(config, messageId, folder = 'INBOX') {
  return withImap(config, async (imap) => {
    await openFolder(imap, folder, true);

    return new Promise((resolve, reject) => {
      const f = imap.fetch([messageId], { bodies: '', struct: true });
      let raw = '';

      f.on('message', (msg) => {
        msg.on('body', (stream) => {
          stream.on('data', (chunk) => { raw += chunk.toString('utf8'); });
        });
      });

      f.once('error', reject);
      f.once('end', async () => {
        try {
          const mail = await simpleParser(raw);
          const attachments = (mail.attachments || []).map((a) => ({
            filename: a.filename,
            contentType: a.contentType,
            size: a.size,
            content: a.content.toString('base64'),
          }));
          resolve(attachments);
        } catch (err) {
          reject(err);
        }
      });
    });
  });
}
