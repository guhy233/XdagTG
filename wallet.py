import threading
import queue
import telebot
import json
import hashlib
import ecdsa
import base58
import requests
import time
import os
import trans
import crypt

class WalletEventsPro(threading.Thread):

    def __init__(self, bot):
        super(WalletEventsPro, self).__init__()
        self.waiting_sent = queue.Queue()
        self.waiting_getbalance = queue.Queue()
        self.bot = bot
    
    def run(self):
        while True:
            if not self.waiting_sent.empty():
                self.process()
            if not self.waiting_getbalance.empty():
                self.get_balance()
            time.sleep(0.1)
            
    def process(self):
        trans_data = self.waiting_sent.get()
        try:
            res = trans.make_trans(trans_data["fromaddress"], trans_data["privatekey"], trans_data["toaddress"], trans_data["value"], trans_data["remark"])
        except Exception:
            self.bot.send_message(trans_data["chatid"], f"Invalid recipient address: Length must be 31 characters and only contain numbers and letters.")
            return
        for _ in range(3):
            if len(res) == 32:
                break
            res = trans.make_trans(trans_data["fromaddress"], trans_data["privatekey"], trans_data["toaddress"], trans_data["value"], trans_data["remark"])
        if len(res) == 32:
            self.bot.send_message(trans_data["chatid"], f"Xdag has been sent. View in explorer: https://mainnet-explorer.xdagj.org/block/{res}")
        else:
            self.bot.send_message(trans_data["chatid"], f"Xdag failed to send, please try again.")

    def add_trans(self, trans: dict):
        self.waiting_sent.put(trans)

    def add_check_balance(self, checkData: dict):
        self.waiting_getbalance.put(checkData)

    def create_wallet(self, private_key=None):
        if private_key == None:
            private_key = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1)
        else:
            try:
                private_key = ecdsa.SigningKey.from_string(bytes.fromhex(private_key), curve=ecdsa.SECP256k1)
            except Exception as e:
                raise ValueError("Wrong Private Key")
            
        public_key = private_key.get_verifying_key()

        x = public_key.pubkey.point.x()
        y = public_key.pubkey.point.y()

        prefix = b'\x02' if y % 2 == 0 else b'\x03'
        x_bin = x.to_bytes(32, 'big')
        compressed_pubkey = prefix + x_bin

        sha256_hash = hashlib.sha256(compressed_pubkey).digest()
        ripemd160_hash = hashlib.new('ripemd160', sha256_hash).digest()
        pubkey_hash = ripemd160_hash
        checksum = hashlib.sha256(hashlib.sha256(pubkey_hash).digest()).digest()[:4]
        address_bin = pubkey_hash + checksum
        address_str = base58.b58encode(address_bin).decode()
        return (private_key.to_string().hex(), address_str)
    
    def get_balance(self):
        get_balance_data = self.waiting_getbalance.get()
        res = json.loads(requests.get(f"https://explorer.xdag.io/api/balance/{get_balance_data["address"]}").text)
        if "error" in res.keys():
            return "err"
        self.bot.send_message(get_balance_data["chatid"], f"{res["balance"]} XDAG")
        return str(res["balance"])


class Bot: 
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token)
        self.walletEventsPro = WalletEventsPro(self.bot)
        self.walletEventsPro.start()
        with open("walletData.json", "r") as file:
            self.walletData = json.load(file)

    def run_bot(self):
        @self.bot.message_handler(commands=["test"])
        def test(message):
            self.bot.reply_to(message, "This is a test message")

        @self.bot.message_handler(commands=["start"])
        def start(message):
            help_command(message)
            userid = str(message.from_user.id)

            if userid not in self.walletData.keys():
                self.show_help(message)
            else:
                self.bot.send_message(message.chat.id, "Welcome back! Your wallet is ready to use.")

        @self.bot.message_handler(commands=["create"])
        def create_wallet(message):
            self.handle_wallet_creation(message)

        @self.bot.message_handler(commands=["recover_privatekey"])
        def recover_privatekey(message):
            self.handle_wallet_recovery(message, self.process_private_key, "pk")

        @self.bot.message_handler(commands=["recover_walletid"])
        def recover_walletid(message):
            self.handle_wallet_recovery(message, self.process_wallet_id, "wid")

        @self.bot.message_handler(commands=["help"])
        def help_command(message):
            help_text = (
                "Available Commands:\n"
                "/balance - Check your wallet balance.\n"
                "/address - Get your wallet address.\n"
                "/privatekey - Get your private key.\n"
                "/send address value [remark] - Send value to a specified address.\n"
                "/help - Show this help message."
            )
            self.bot.reply_to(message, help_text)

        @self.bot.message_handler(commands=["balance"])
        def balance(message):
            userid = str(message.from_user.id)
            if userid not in self.walletData:
                self.show_help(message)
                return
            wallet_id = self.walletData[userid]
            wallet_info = self.get_wallet_info(wallet_id, message)
            if wallet_info:
                address = wallet_info["address"]         
                checkData = {
                    "address": address,
                    "chatid": message.chat.id
                }
                self.walletEventsPro.add_check_balance(checkData)
                self.bot.reply_to(message, "Your balance:")

        @self.bot.message_handler(commands=["address"])
        def address(message):
            userid = str(message.from_user.id)       
            if userid not in self.walletData:
                self.show_help(message)
                return
            wallet_id = self.walletData[userid]
            wallet_info = self.get_wallet_info(wallet_id, message)
            if wallet_info:
                self.bot.reply_to(message, f"{wallet_info["address"]}")

        @self.bot.message_handler(commands=["privatekey"])
        def privatekey(message):
            userid = str(message.from_user.id)
            if userid not in self.walletData:
                self.show_help(message)
                return
            self.bot.reply_to(message, "Please enter your wallet password:")
            self.bot.register_next_step_handler(message, self.verify_password_for_privatekey, userid)
        
        @self.bot.message_handler(commands=["send"])
        def send(message):
            try:
                args = message.text.split()[1:]  # 去掉命令部分，获取参数
                if len(args) < 2:
                    self.bot.reply_to(message, "Usage: /send address value [remark]")
                    return
                toaddress = args[0]
                value = float(args[1])
                remark = args[2] if len(args) > 2 else ""
                userid = str(message.from_user.id)
                if userid not in self.walletData:
                    self.show_help(message)
                    return
                wallet_id = self.walletData[userid]
                wallet_info = self.get_wallet_info(wallet_id, message)
                if wallet_info:
                    fromaddress = wallet_info["address"]
                    privatekey = wallet_info["privatekey"]
                    self.bot.reply_to(message, "Please enter your wallet password:")
                    self.bot.register_next_step_handler(message, self.verify_password_and_send, toaddress, value, remark, fromaddress, privatekey, wallet_info["password"])
            except IndexError:
                self.bot.reply_to(message, "Invalid command format. Usage: /send address value [remark]")
            except ValueError:
                self.bot.reply_to(message, "Invalid value format. Please enter a numeric value.")

        self.bot.infinity_polling()

    def save_wallet_info(self, wallet_id, address, private_key, password):
        wallet_info = {
            "address": address,
            "privatekey": crypt.aes_encrypt(password, private_key),
            "password": crypt.sha256(password)
        }
        with open(f"{wallet_id}.json", "w") as file:
            json.dump(wallet_info, file)

    def save_wallet_data(self):
        with open("walletData.json", "w") as file:
            json.dump(self.walletData, file)
    
    def get_wallet_info(self, wallet_id, message):
        wallet_file = f"{wallet_id}.json"
        wallet_info = None
        try:
            with open(wallet_file, "r") as file:
                wallet_info = json.load(file)
        except FileNotFoundError:
            self.bot.reply_to(message, "Wallet file not found. ")
            self.show_help(message)
        return wallet_info
    
    def show_help(self, message):
        self.bot.send_message(
        message.chat.id,
        "It seems you don't have a wallet yet. Please use the following commands to proceed:\n"
        "/create - Create a new wallet.\n"
        "/recover_privatekey - Recover your wallet using your private key.\n"
        "/recover_walletid - Recover your wallet using your wallet ID."
        )

    def verify_password_and_send(self, message, toaddress, value, remark, fromaddress, privatekey, correct_password):
        password = message.text
        if crypt.sha256(password) == correct_password:
            trans = {
                "fromaddress": fromaddress,
                "toaddress": toaddress,
                "privatekey": crypt.aes_decrypt(password, privatekey),
                "value": value,
                "remark": remark,
                "chatid": message.chat.id
            }
            self.bot.reply_to(message, f"Transaction initiated: {value} XDAG sent to {toaddress}\nRemark: {remark}")
            self.walletEventsPro.add_trans(trans)
        else:
            self.bot.reply_to(message, "Incorrect password. Transaction cancelled.")

    def process_wallet_id(self, message, user_id):
        wallet_id = message.text
        if wallet_id in self.walletData.values():
            self.bot.send_message(message.chat.id, "Please send your password:")
            self.bot.register_next_step_handler(message, self.verify_password, user_id, wallet_id)
        else:
            self.bot.send_message(message.chat.id, "Invalid wallet ID. Operation cancelled. Please re-enter the command to try again.")

    def verify_password(self, message, user_id, wallet_id):
        password = message.text
        wallet_info = self.get_wallet_info(wallet_id, message)
        if wallet_info:
            if wallet_info["password"] == crypt.sha256(password):
                self.walletData[user_id] = wallet_id
                self.save_wallet_data()
                self.bot.send_message(message.chat.id, f"Wallet recovered.\nUserid: {user_id}\nWallet ID: {wallet_id}\nAddress: {wallet_info['address']}")
            else:
                self.bot.send_message(message.chat.id, "Incorrect password. Operation cancelled. Please re-enter the command to try again.")
                return 

    def handle_wallet_creation(self, message):
        userid = str(message.from_user.id)
        
        if userid in self.walletData:
            self.bot.reply_to(message, "You already have a wallet.")
        else:
            self.bot.send_message(message.chat.id, "Please send the password for your new wallet:")
            private_key, address = self.walletEventsPro.create_wallet()
            self.bot.register_next_step_handler(message, self.process_password, userid, address, private_key)

    def handle_wallet_recovery(self, message, process_function, op):
        userid = str(message.from_user.id)
        
        if userid in self.walletData:
            self.bot.reply_to(message, "You already have a wallet. Use /create if you want to create a new wallet.")
        else:
            if op == "pk":
                self.bot.send_message(message.chat.id, "Please send your private key:")
            else:
                self.bot.send_message(message.chat.id, "Please send your walletID:")
            self.bot.register_next_step_handler(message, process_function, userid)

    def process_password(self, message, user_id, address, private_key):
        self.request_password_confirmation(message, user_id, address, private_key, self.confirm_password_creation)

    def confirm_password_creation(self, message, user_id, address, private_key, password):
        if password != message.text:
            self.bot.send_message(message.chat.id, "Passwords do not match. Please re-enter the command to try again.")
            return
        wallet_id = str(len(self.walletData))
        self.save_wallet_info(wallet_id, address, private_key, password)
        self.walletData[user_id] = wallet_id
        self.save_wallet_data()
        self.bot.send_message(message.chat.id, f"Wallet created.\nUserid: {user_id}\nWallet ID: {wallet_id}\nAddress: {address}")

    def request_password_confirmation(self, message, user_id, private_key, address, confirmation_function):
        password = message.text
        self.bot.send_message(message.chat.id, "Please re-enter your password to confirm:")
        self.bot.register_next_step_handler(message, confirmation_function, user_id, private_key, address, password)

    def process_private_key(self, message, user_id):
        private_key = message.text
        try:
            private_key, address = self.walletEventsPro.create_wallet(private_key)
            self.bot.send_message(message.chat.id, "Please send your password:")
            self.bot.register_next_step_handler(message, self.request_password_confirmation, user_id, private_key, address, self.confirm_password_recovery)
        except ValueError:
            self.bot.send_message(message.chat.id, "Invalid private key. Operation cancelled. Please re-enter the command to try again.")

    def confirm_password_recovery(self, message, user_id, private_key, address, password):
        if password != message.text:
            self.bot.send_message(message.chat.id, "Passwords do not match. Operation cancelled. Please re-enter the command to try again.")
            return
        wallet_id = str(len(self.walletData))
        self.save_wallet_info(wallet_id, address, private_key, password)
        self.walletData[user_id] = wallet_id
        self.save_wallet_data()
        self.bot.send_message(message.chat.id, f"Wallet recovered.\nUserid: {user_id}\nWallet ID: {wallet_id}\nAddress: {address}")
        
    def verify_password_for_privatekey(self, message, user_id):
        password = message.text
        wallet_id = self.walletData[user_id]
        wallet_info = self.get_wallet_info(wallet_id, message)

        if wallet_info:
            if wallet_info["password"] == crypt.sha256(password):
                privatekey = wallet_info["privatekey"]
                self.bot.reply_to(message, crypt.aes_decrypt(password, privatekey))
            else:
                self.bot.reply_to(message, "Incorrect password. Operation cancelled.")


def main():
    bot = Bot(os.getenv("BOT_TOKEN"))
    bot.run_bot()


if __name__ == "__main__":
    main()
