"""Keys and transactions. The main key comes only from the secrets volume or the environment, never from the repo."""
import base64
import json
import os
import pathlib

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

MEMO = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
LAMPORTS = 1_000_000_000


def _parse_key(text):
    text = text.replace("\ufeff", "").strip().strip('"')  # Notepad may add a BOM or quotes
    if text.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(text)))
    return Keypair.from_base58_string(text)


def main_key(secrets_dir):
    """ITB_SECRET_KEY (base58 or JSON array) or <secrets>/keypair.json (solana-keygen format). None if absent."""
    env = os.environ.get("ITB_SECRET_KEY")
    if env:
        return _parse_key(env)
    p = pathlib.Path(secrets_dir) / "keypair.json"
    if p.exists():
        return _parse_key(p.read_text())
    return None


def sub_keys(secrets_dir, n, create=False):
    """Extra fee payers for the series without shared write locks. Stored next to the main key."""
    p = pathlib.Path(secrets_dir) / "subwallets.json"
    keys = [Keypair.from_bytes(bytes(k)) for k in json.loads(p.read_text())] if p.exists() else []
    if len(keys) < n and create:
        keys += [Keypair() for _ in range(n - len(keys))]
        p.write_text(json.dumps([list(bytes(k)) for k in keys]))
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return keys[:n]


def memo_text(burst, k):
    """Fixed length (9 bytes) so every probe costs the same compute: 'itb' + 5-digit burst + 1 hex index."""
    return "itb%05d%x" % (burst % 100000, k % 16)


def probe_tx(payer, blockhash, cu_limit, cu_price, memo):
    """A probe: compute-unit limit, compute-unit price, memo. The only writable account is the fee payer."""
    ixs = [set_compute_unit_limit(cu_limit), set_compute_unit_price(cu_price),
           Instruction(MEMO, memo.encode(), [])]
    msg = Message.new_with_blockhash(ixs, payer.pubkey(), blockhash)
    return Transaction([payer], msg, blockhash)


def transfer_tx(src, dst_pubkey, lamports, blockhash):
    ix = transfer(TransferParams(from_pubkey=src.pubkey(), to_pubkey=dst_pubkey, lamports=lamports))
    return Transaction([src], Message.new_with_blockhash([ix], src.pubkey(), blockhash), blockhash)


def b64(tx):
    return base64.b64encode(bytes(tx)).decode()


def fee_estimate(cu_limit, cu_price, signatures=1):
    """Lamports: 5000 per signature + ceil(price * limit / 1e6)."""
    return 5000 * signatures + (cu_price * cu_limit + 999_999) // 1_000_000


def to_hash(s):
    return Hash.from_string(s)
