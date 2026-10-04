/**
 * WebAuthn ceremonies (security keys, passkeys): the server sends JSON options with base64url fields, the
 * browser API wants ArrayBuffers, and the answer goes back as JSON with base64url fields.
 */

type Json = Record<string, unknown>;

const toBuffer = (b64url: string): ArrayBuffer => {
  const b64 = b64url.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((b64url.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
};

const toB64url = (buf: ArrayBuffer | null | undefined): string | null => {
  if (!buf) return null;
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

const descriptors = (list: unknown) =>
  ((list as Json[] | undefined) ?? []).map((d) => ({ ...d, id: toBuffer(d.id as string) })) as PublicKeyCredentialDescriptor[];

export const webauthnSupported = () => typeof window !== 'undefined' && !!window.PublicKeyCredential && window.isSecureContext;

function explain(e: unknown): Error {
  const name = (e as DOMException)?.name;
  if (name === 'NotAllowedError') return new Error('The security key request was cancelled or timed out');
  if (name === 'InvalidStateError') return new Error('This security key is already registered for your account');
  if (name === 'SecurityError') return new Error('Security keys need HTTPS and the address set in REFEX_PUBLIC_URL');
  return e instanceof Error ? e : new Error(String(e));
}

/** Registration: options from the server -> credential to send back. */
export async function createCredential(options: Json): Promise<Json> {
  const user = options.user as Json;
  const publicKey = {
    ...options,
    challenge: toBuffer(options.challenge as string),
    user: { ...user, id: toBuffer(user.id as string) },
    excludeCredentials: descriptors(options.excludeCredentials),
  } as unknown as PublicKeyCredentialCreationOptions;
  let cred: PublicKeyCredential;
  try {
    cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential;
  } catch (e) {
    throw explain(e);
  }
  const response = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: {
      clientDataJSON: toB64url(response.clientDataJSON),
      attestationObject: toB64url(response.attestationObject),
      transports: response.getTransports?.() ?? [],
    },
  };
}

/** Authentication: options from the server -> assertion to send back. */
export async function getAssertion(options: Json): Promise<Json> {
  const publicKey = {
    ...options,
    challenge: toBuffer(options.challenge as string),
    allowCredentials: descriptors(options.allowCredentials),
  } as unknown as PublicKeyCredentialRequestOptions;
  let cred: PublicKeyCredential;
  try {
    cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential;
  } catch (e) {
    throw explain(e);
  }
  const response = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: {
      clientDataJSON: toB64url(response.clientDataJSON),
      authenticatorData: toB64url(response.authenticatorData),
      signature: toB64url(response.signature),
      userHandle: toB64url(response.userHandle),
    },
  };
}
