import time
import struct
import binascii
from ecdsa import SECP256k1, SigningKey
from hashlib import sha256
import base58 
import requests
import json

XDAG_FIELD_SIZE = 32

def validate_remark(remark):
    return len(remark) <= XDAG_FIELD_SIZE

def xdag2amount(value):
    return int(value * (1 << 32)) 

def get_current_timestamp():
    t = time.time_ns() + 64 # 我也不知道为什么要加这个64，但是不加交易就没法成功
    
    sec = t // 1_000_000_000
    usec = (t - sec * 1_000_000_000) // 1_000
    xmsec = (usec << 10) // 1_000_000
    
    return (sec << 10) | xmsec

def check_base58_address(address):
    try:
        addr_bytes = base58.b58decode(address) 
    except Exception as e:
        raise ValueError(f"Invalid Base58 address: {e}")
    if len(addr_bytes) != 24:
        raise ValueError("Address length error")
    addr_bytes = addr_bytes[:20][::-1] 
    return "00000000" + binascii.hexlify(addr_bytes).decode()

def transaction_sign(block, key, has_remark):
    if has_remark:
        block += "00000000000000000000000000000000" * 22
    else:
        block += "00000000000000000000000000000000" * 24

    pub_key = key.verifying_key.to_string("compressed").hex()
    block += pub_key

    block_bytes = binascii.unhexlify(block)
    block_hash = sha256(sha256(block_bytes).digest()).digest()

    signature = key.sign_digest(block_hash) 

    r, s = signature[:32], signature[32:]

    return r.hex(), s.hex()

def field_types(is_test, is_from_old, has_remark, is_pub_key_even):
    result = []

    result.append("2" if is_from_old else "C")

    result.append("8" if is_test else "1")

    if has_remark:
        result.append("9D560500000000" if is_pub_key_even else "9D570500000000")
    else:
        result.append("6D550000000000" if is_pub_key_even else "7D550000000000")

    return "".join(result)

def transaction_block(from_addr, to_addr, remark, value, key):
    if key is None:
        raise ValueError("Private key is required")

    try:
        in_address = check_base58_address(from_addr)
        is_from_old = False
    except ValueError:
        in_address = from_addr
        is_from_old = True

    try:
        out_address = check_base58_address(to_addr)
    except ValueError as e:
        raise ValueError(f"Invalid recipient address: {e}")

    remark_bytes = bytearray(XDAG_FIELD_SIZE)
    if remark:
        if validate_remark(remark):
            remark_bytes[:len(remark)] = remark.encode()
        else:
            raise ValueError("Remark exceeds maximum size")

    if value <= 0.0:
        raise ValueError("Transaction value must be greater than zero")
    trans_value = xdag2amount(value)
    val_bytes = struct.pack("<Q", trans_value)

    timestamp = get_current_timestamp()
    time_bytes = struct.pack("<Q", timestamp)

    block = []

    block.append("0000000000000000")
    pub_key_compressed = key.verifying_key.to_string("compressed")[0]
    is_pub_key_even = (pub_key_compressed % 2 == 0)
    block.append(field_types(False, is_from_old, bool(remark), is_pub_key_even))

    block.append(time_bytes.hex())
    block.append("0000000000000000")

    block.append(in_address)
    block.append(val_bytes.hex())
    block.append(out_address)
    block.append(val_bytes.hex())

    if remark:
        block.append(remark_bytes.hex())

    block.append(key.verifying_key.to_string("compressed")[1:].hex())

    block_str = "".join(block)
    r, s = transaction_sign(block_str, key, bool(remark))

    block.append(r)
    block.append(s)

    if remark:
        block.append("00000000000000000000000000000000" * 18)
    else:
        block.append("00000000000000000000000000000000" * 20)

    return "".join(block)


def make_trans(from_address, private_key_hex, to_address, value, remark):
    private_key_bytes = binascii.unhexlify(private_key_hex)
    private_key = SigningKey.from_string(private_key_bytes, curve=SECP256k1)
    trans_block = transaction_block(from_address, to_address, remark, value, private_key)
    
    url = "https://mainnet-rpc.xdagj.org"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "jsonrpc": "2.0",
        "method": "xdag_sendRawTransaction",
        "params": [
            f"{trans_block}"
        ],
        "id": 1
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))
    return response.json()["result"]

