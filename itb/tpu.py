"""Direct path: send transactions straight to the TPU address a leader announces in gossip, over QUIC, bypassing RPC.

Solana TPU QUIC: ALPN "solana-tpu", self-signed certificates on both sides (the server's is the validator identity;
ours is a throwaway key, so we connect as an unstaked client), one unidirectional stream per transaction.
"""
import asyncio
import datetime
import ssl
import tempfile
import time

from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

ALPN = "solana-tpu"


def _client_cert_files():
    """Self-signed Ed25519 certificate for an unstaked client, written to temp PEM files."""
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Solana node")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, None))
    d = tempfile.mkdtemp(prefix="itb_quic_")
    cf, kf = d + "/cert.pem", d + "/key.pem"
    open(cf, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(kf, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cf, kf


_FILES = None


def configuration():
    global _FILES
    if _FILES is None:
        _FILES = _client_cert_files()
    cfg = QuicConfiguration(is_client=True, alpn_protocols=[ALPN], verify_mode=ssl.CERT_NONE,
                            idle_timeout=10.0, server_name="localhost")
    cfg.load_cert_chain(*_FILES)
    return cfg


class TpuConnection:
    """One QUIC connection to one TPU address. send() opens a uni stream per transaction and returns at once."""

    def __init__(self, addr):
        self.host, port = addr.rsplit(":", 1)
        self.port = int(port)
        self._cm = None
        self.proto = None

    async def open(self, timeout=3.0):
        self._cm = connect(self.host, self.port, configuration=configuration(), wait_connected=True)
        self.proto = await asyncio.wait_for(self._cm.__aenter__(), timeout)
        return self

    def send(self, tx_bytes):
        q = self.proto._quic
        sid = q.get_next_available_stream_id(is_unidirectional=True)
        q.send_stream_data(sid, tx_bytes, end_stream=True)
        self.proto.transmit()
        return sid

    async def close(self):
        if self._cm:
            try:
                await asyncio.wait_for(self._cm.__aexit__(None, None, None), 2.0)
            except Exception:  # noqa: BLE001
                pass


async def handshake(addr, timeout=3.0):
    """(ok, milliseconds or error) for a bare connect to a TPU address."""
    t0 = time.perf_counter()
    c = TpuConnection(addr)
    try:
        await c.open(timeout)
        return True, round((time.perf_counter() - t0) * 1000, 1)
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:80])
    finally:
        await c.close()
