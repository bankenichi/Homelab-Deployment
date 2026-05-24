/**
 * SMTP client for Proton Bridge
 * Handles send and reply operations via nodemailer
 */

import nodemailer from 'nodemailer';
import { getMessageHeaders, getMessage as getFullMessage } from './imap-client.js';

function createTransporter(config) {
  return nodemailer.createTransport({
    host: config.smtp_host || '127.0.0.1',
    port: config.smtp_port || 1025,
    secure: false,
    auth: {
      user: config.username,
      pass: config.password,
    },
    tls: { rejectUnauthorized: false },
  });
}

function isHtml(str) {
  return str && /<[a-z][\s\S]*>/i.test(str);
}

function htmlToPlain(str) {
  return str
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .trim();
}

export async function sendMessage(config, { to, subject, body, html, cc, bcc, attachments, from }) {
  const transporter = createTransporter(config);

  // Use per-call override → configured from address → username fallback
  const sender = from || config.from || config.username;

  const sig = config.signature || '';
  const sigIsHtml = isHtml(sig);

  const mailOpts = {
    from: sender,
    to,
    cc,
    bcc,
    subject,
    attachments,
  };

  if (html) {
    // Caller explicitly provided HTML body
    mailOpts.html = html + (sig ? `<br><br>${sigIsHtml ? sig : sig.replace(/\n/g, '<br>')}` : '');
    mailOpts.text = body + (sig ? '\n\n' + (sigIsHtml ? htmlToPlain(sig) : sig) : '');
  } else if (sigIsHtml) {
    // Plain-text body but HTML signature — upgrade to HTML so signature renders correctly
    const htmlBody = body.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
    mailOpts.html = htmlBody + `<br><br>${sig}`;
    mailOpts.text = body + '\n\n' + htmlToPlain(sig);
  } else {
    // Both body and signature are plain text
    mailOpts.text = body + (sig ? `\n\n${sig}` : '');
  }

  const info = await transporter.sendMail(mailOpts);
  return { success: true, message_id: info.messageId };
}

export async function forwardMessage(config, { originalMessageId, to, body, cc, bcc, folder, from }) {
  const original = await getFullMessage(config, originalMessageId, folder || 'INBOX');

  const fwdSubject = original.subject.startsWith('Fwd:')
    ? original.subject
    : `Fwd: ${original.subject}`;

  const fwdBody = `${body || ''}\n\n---------- Forwarded message ----------\nFrom: ${original.from}\nDate: ${original.date}\nSubject: ${original.subject}\nTo: ${original.to}\n\n${original.body}`;

  const transporter = createTransporter(config);

  // Use per-call override → configured From Email → bridge username fallback,
  // matching sendMessage's behavior. Previously forwarded mail was hardcoded to
  // come from config.username (the Proton account email), ignoring config.from.
  const sender = from || config.from || config.username;

  const info = await transporter.sendMail({
    from: sender,
    to,
    cc,
    bcc,
    subject: fwdSubject,
    text: fwdBody,
  });

  return { success: true, message_id: info.messageId };
}

export async function replyMessage(config, { originalMessageId, body, cc, bcc, replyAll, attachments, folder, from }) {
  const headers = await getMessageHeaders(config, originalMessageId, folder || 'INBOX');

  const replySubject = headers.subject.startsWith('Re:')
    ? headers.subject
    : `Re: ${headers.subject}`;

  // Build References chain: existing refs + original Message-ID.
  // mailparser returns `references` as a STRING for a single reference and an
  // ARRAY for multiple — normalize before spreading or we corrupt the header.
  const rawRefs = headers.references;
  const refsArr = Array.isArray(rawRefs) ? rawRefs : (rawRefs ? [rawRefs] : []);
  const refsList = [...refsArr, headers.messageId].filter(Boolean);
  const referencesStr = refsList.join(' ');

  // Reply-all: include original To and CC recipients (excluding ourselves)
  let to = headers.from;
  if (replyAll) {
    const allRecipients = [headers.from, headers.to, headers.cc].filter(Boolean).join(', ');
    // Remove our own address to avoid sending to ourselves
    const ownAddr = config.username.toLowerCase();
    to = allRecipients
      .split(/,\s*/)
      .filter((addr) => !addr.toLowerCase().includes(ownAddr))
      .join(', ') || headers.from;
  }

  const transporter = createTransporter(config);

  const sig = config.signature || '';
  const sigIsHtml = isHtml(sig);

  // Use per-call override → configured From Email → bridge username fallback,
  // matching sendMessage's behavior. Previously replies were hardcoded to
  // come from config.username (the Proton account email), ignoring config.from.
  const sender = from || config.from || config.username;

  const replyOpts = {
    from: sender,
    to,
    cc,
    bcc,
    subject: replySubject,
    inReplyTo: headers.messageId,
    references: referencesStr,
    attachments,
  };

  if (sigIsHtml) {
    const htmlBody = body.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
    replyOpts.html = `${htmlBody}<br><br>${sig}`;
    replyOpts.text = body + '\n\n' + htmlToPlain(sig);
  } else {
    replyOpts.text = body + (sig ? `\n\n${sig}` : '');
  }

  const info = await transporter.sendMail(replyOpts);

  return { success: true, message_id: info.messageId };
}
