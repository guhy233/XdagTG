import threading
import queue
import telebot
import json
import hashlib
import trans
import requests
import time
import random


def safe_read(filename, lock):
    with lock:
        with open(filename, "r") as file:
            return json.load(file)


def safe_write(filename, data, lock):
    with lock:
        with open(filename, "w") as file:
            json.dump(data, file)


class WalletEventsPro(threading.Thread):

    def __init__(self, users, wallet, bot, last_trans, file_lock):
        super(WalletEventsPro, self).__init__()
        self.users = users
        self.wallet = wallet
        self.last_trans = last_trans
        self.bot = bot
        self.waiting = queue.Queue()
        self.lock = threading.RLock()
        self.file_lock = file_lock

    def run(self):
        while True:
            self.update_balance()
            for _ in range(120):
                if not self.waiting.empty():
                    self.process_withdraw()
                time.sleep(0.5)

    def update_balance(self):
        try:
            res = json.loads(requests.get(
                f"https://explorer.xdag.io/api/block/{self.wallet['ad']}").text)
        except Exception as e:
            print(f"Error fetching balance: {e}")
            return

        with self.lock:
            for trans in res.get("block_as_address", []):
                print(trans)
                if hashlib.md5(str(trans).encode()).hexdigest() == self.last_trans:
                    break

                if trans["direction"] == "input":
                    userid = trans.get("remark")
                    amount = float(trans.get("amount", 0))

                    if userid in self.users and userid != "-1":
                        self.users[userid] = round(self.users[userid] + amount, 9)
                        self.bot.send_message(userid, f"You received {amount} XDAG")
                        self.bot.send_message(userid, f"View in explorer: https://explorer.xdag.io/block/{trans['address']}")
                    else:
                        self.users["-1"] = round(self.users["-1"] + amount, 9)

            safe_write("users.json", self.users, self.file_lock)

        if res.get("block_as_address"):
            self.last_trans = hashlib.md5(str(res["block_as_address"][0]).encode()).hexdigest()
            config = safe_read("config.json", self.file_lock)
            config["last_trans"] = self.last_trans
            safe_write("config.json", config, self.file_lock)

    def add_withdraw(self, address, value, remark, userid):
        self.waiting.put([address, value, remark, userid])

    def process_withdraw(self):
        trans_data = self.waiting.get()
        try:
            res = trans.make_trans(self.wallet["ad"], self.wallet["pk"], trans_data[0], trans_data[1], trans_data[2])
        except Exception as e:
            self.bot.send_message(trans_data[3], "Invalid recipient address: Length must be 31 characters and only contain numbers and letters.")
            print(f"Error during transaction: {e}")
            return

        for _ in range(3):
            if len(res) == 32:
                break
            res = trans.make_trans(self.wallet["ad"], self.wallet["pk"], trans_data[0], trans_data[1], trans_data[2])

        with self.lock:
            if len(res) == 32:
                self.users[trans_data[3]] = round(self.users[trans_data[3]] - trans_data[1], 9)
                safe_write("users.json", self.users, self.file_lock)
                msg = f"XDAG has been sent. View in explorer: https://mainnet-explorer.xdagj.org/block/{res}"
                self.bot.send_message(trans_data[3], msg)
            else:
                self.bot.send_message(trans_data[3], "XDAG failed to send, please try again.")


class Bot:

    def __init__(self):
        self.file_lock = threading.Lock()
        config = safe_read("config.json", self.file_lock)
        self.bot = telebot.TeleBot(config["token"])
        self.wallet = {"pk": config["private_key"], "ad": config["address"]}
        last_trans = config["last_trans"]
        self.users = safe_read("users.json", self.file_lock)
        self.usernames = safe_read("usernames.json", self.file_lock)
        self.walletEventsPro = WalletEventsPro(self.users, self.wallet, self.bot, last_trans, self.file_lock)
        self.walletEventsPro.start()

    def run_bot(self):
        @self.bot.message_handler(commands=["test"])
        def test(message):
            print(9)
            self.bot.reply_to(message, "This is a test message")
        
        @self.bot.message_handler(commands=["help"])
        def help(message):
            help_text = (
                "/register - User registration. \n"
                "/deposit - Get your deposit address and identifier for depositing funds.\n"
                "/update - Update your username information. If you are registered and have a set username, this will update your info.\n"
                "/balance - Check your account balance.\n"
                "/transfer @username value - Transfer the specified amount of XDAG to the specified username.\n"
                "/withdraw address value [remark] - Withdraw to a specified address with the amount and optional remark.\n"
                "/hongbao value amount - Create a red packet with a total value and number of packets. Other users can grab the red packet.[GROUP ONLY]\n"
            )
            self.bot.reply_to(message, help_text)
        
        @self.bot.message_handler(commands=["start"])
        def start(message):
            help(message)

        @self.bot.message_handler(commands=["register"])
        def register(message):
            if message.chat.type in ["group", "supergroup"]:
                return
            userid = str(message.from_user.id)
            username = message.from_user.username
            if userid in self.users:
                self.bot.reply_to(message, "You have already registered.")
                return
            self.update_user(userid, username)
            self.bot.reply_to(message, "Registered.")
            self.bot.send_message(userid, f"Deposit Address: {self.wallet['ad']}\nMemo/Tag Identifier: {userid}")
            if not username:
                self.bot.send_message(userid, "You have not set or have hidden your username, limiting certain features.")

        @self.bot.message_handler(commands=["deposit"])
        def deposit_help(message):
            if message.chat.type in ["group", "supergroup"]:
                return
            userid = str(message.from_user.id)
            if userid not in self.users:
                self.bot.reply_to(message, "You are not registered. Use /register to register.")
                return
            self.bot.send_message(userid, f"Deposit Address: {self.wallet['ad']}\nMemo/Tag Identifier: {userid}")

        @self.bot.message_handler(commands=["update"])
        def update(message):
            if message.chat.type in ["group", "supergroup"]:
                return
            userid = str(message.from_user.id)
            username = message.from_user.username
            if userid not in self.users:
                self.bot.reply_to(message, "You are not registered. Use /register to register.")
                return
            if not username:
                self.bot.reply_to(message, "You have not set or have hidden your username, so some features will be unavailable.")
                return
            self.update_user(userid, username, self.users[userid])
            self.bot.reply_to(message, "Updated.")

        @self.bot.message_handler(commands=["balance"])
        def check_balance(message):
            if message.chat.type in ["group", "supergroup"]:
                return
            userid = str(message.from_user.id)
            if userid not in self.users:
                self.bot.reply_to(message, "You are not registered. Use /register to register.")
                return
            self.bot.reply_to(message, f"Your balance: {self.users[userid]}")

        @self.bot.message_handler(commands=["transfer"])
        def transfer(message):
            userid = str(message.from_user.id)
            username = message.from_user.username
            if userid not in self.users:
                self.bot.reply_to(message, "You are not registered. Use /register to register.")
                return
            args = message.text.split()[1:]
            if len(args) != 2:
                self.bot.reply_to(message, "Invalid command format. Usage: /transfer @username value")
                return
            try:
                target, value = args
                target = target[1:]
                value = float(value)
                if value <= 0:
                    self.bot.reply_to(message, "Invalid value. The value must be a positive number.")
                    return
                if target in self.usernames:
                    if self.users[userid] >= value:
                        self.users[userid] = round(self.users[userid] - value, 9)
                        self.users[self.usernames[target]] = round(self.users[self.usernames[target]] + value, 9)
                        self.bot.reply_to(message, "Transfer successful.")
                        safe_write("users.json", self.users, self.file_lock)
                        if username:
                            self.bot.send_message(self.usernames[target], f"You received {value} XDAG from @{username}.")
                        else:
                            self.bot.send_message(self.usernames[target], f"You received {value} XDAG.")
                    else:
                        self.bot.reply_to(message, "Insufficient balance.")
                else:
                    self.bot.reply_to(message, "The user does not exist or has not yet registered.")
            except ValueError:
                self.bot.reply_to(message, "Invalid value. The value must be a positive number.")
        
        @self.bot.message_handler(commands=["withdraw"])
        def withdraw(message):
            if message.chat.type in ["group", "supergroup"]:
                return
            userid = str(message.from_user.id)
            if userid not in self.users:
                self.show_help(message)
                return
            try:
                args = message.text.split()[1:]  # 去掉命令部分，获取参数
                if len(args) < 2:
                    self.bot.reply_to(message, "Usage: /send address value [remark]")
                    return
                address = args[0]
                value = float(args[1])
                remark = args[2] if len(args) > 2 else ""
                print(address)
                if value <= 0.1:
                    self.bot.reply_to(message, "Invalid value. The value must be a positive number and greater than 0.1.")
                    return
                if value > self.users[userid]: 
                    self.bot.reply_to(message, "Insufficient balance.")
                    return
                self.bot.reply_to(message, f"Transaction initiated: {value} XDAG sent to {address}\nRemark: {remark}")
                self.walletEventsPro.add_withdraw(address, value, remark, userid)
            except IndexError:
                self.bot.reply_to(message, "Invalid command format. Usage: /send address value [remark]")
            except ValueError:
                self.bot.reply_to(message, "Invalid value format. Please enter a numeric value.")

        @self.bot.message_handler(commands=["hongbao"])
        def hongbao(message):
            if message.chat.type not in ["group", "supergroup"]:
                return
            userid = str(message.from_user.id)
            if userid not in self.users:
                self.bot.reply_to(message, "You are not registered. Use /register to register.")
                return
            args = message.text.split()[1:]
            if len(args) != 2:
                self.bot.reply_to(message, "Usage: /hongbao value amount (value is the total amount in XDAG, amount is the number of packets)")
                return
            try:
                value = float(args[0])
                amount = int(args[1])
                if value <= 0 or amount <= 0:
                    self.bot.reply_to(message, "Value and amount must be positive numbers.")
                    return
                if value > self.users.get(userid, 0):
                    self.bot.reply_to(message, "Insufficient balance.")
                    return
                total_value = int(value * 1000000000)
                packets = []
                for _ in range(amount - 1):
                    packet_value = random.randint(1, total_value // (amount - len(packets)))
                    packets.append(packet_value)
                    total_value -= packet_value
                packets.append(total_value)
                red_packet_id = hashlib.md5(f"{time.time()}".encode()).hexdigest()
                red_packet_data = {
                    "total_value": value,
                    "amount": amount,
                    "packets": [packet / 1000000000 for packet in packets],
                    "remaining": amount,
                    "users": []
                }
                red_packets = safe_read("hongbao.json", self.file_lock)
                red_packets[red_packet_id] = red_packet_data
                safe_write("hongbao.json", red_packets, self.file_lock)
                markup = telebot.types.InlineKeyboardMarkup()
                button = telebot.types.InlineKeyboardButton(text="Grab Red Packet", callback_data=f"grab_{red_packet_id}")
                markup.add(button)
                self.bot.send_message(message.chat.id, f"A red packet with a total of {value:.9f} XDAG is available. Grab it!", reply_markup=markup)
                self.users[userid] = round(self.users[userid] - value, 9)
                safe_write("users.json", self.users, self.file_lock)
            except ValueError:
                self.bot.reply_to(message, "Invalid value. Please enter numeric values.")

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith("grab_"))
        def handle_red_packet_grab(callback_query):
            userid = str(callback_query.from_user.id)
            if userid not in self.users:
                self.bot.answer_callback_query(callback_query.id, "You are not registered. Use /register to register.")
                return
            red_packet_id = callback_query.data.split("_")[1]
            red_packets = safe_read("hongbao.json", self.file_lock)
            red_packet_data = red_packets.get(red_packet_id)
            if not red_packet_data:
                self.bot.answer_callback_query(callback_query.id, "This red packet is no longer available.")
                return
            if userid in red_packet_data["users"]:
                self.bot.answer_callback_query(callback_query.id, "You have already grabbed this red packet.")
                return
            if red_packet_data["remaining"] <= 0:
                self.bot.answer_callback_query(callback_query.id, "Sorry, no more packets are available.")
                return
            if red_packet_data["packets"]:
                grabbed_packet = random.choice(red_packet_data["packets"])
                red_packet_data["packets"].remove(grabbed_packet)
                red_packet_data["remaining"] -= 1
                red_packet_data["users"].append(userid)
                safe_write("hongbao.json", red_packets, self.file_lock)
                self.users[userid] = round(self.users.get(userid, 0) + grabbed_packet, 9)
                safe_write("users.json", self.users, self.file_lock)
                self.bot.answer_callback_query(callback_query.id, f"You grabbed {grabbed_packet:.9f} XDAG from a red packet.")
                self.bot.send_message(callback_query.from_user.id, f"You grabbed {grabbed_packet:.9f} XDAG from a red packet.")
            else:
                self.bot.answer_callback_query(callback_query.id, "This red packet is empty.")

        self.bot.infinity_polling()
    
    def update_user(self, user, username, amount = 0):
        self.users[user] = amount
        if username != None:
            self.usernames[username] = user
            safe_write("usernames.json", self.usernames, self.file_lock)
        safe_write("users.json", self.users, self.file_lock)
        
    def show_help(self, message):
        self.bot.reply_to(message, "It seems you haven't registered yet. Use /register to register.")


def main():
    bot = Bot()
    bot.run_bot()


if __name__ == "__main__":
    main()
