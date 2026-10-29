from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Callback data constants
CALLBACK_NOW = "action:now"
CALLBACK_METHOD_CASH = "method:cash"
CALLBACK_METHOD_SATISPAY = "method:satispay"
CALLBACK_METHOD_SUMUP = "method:sumup"
CALLBACK_FLOW_IN = "flow:in"
CALLBACK_FLOW_OUT = "flow:out"
CALLBACK_BOX_USE_LAST = "box:use_last"
CALLBACK_BOX_CONFIRM_CALCULATED = "box:confirm_calculated"
CALLBACK_RECEIPT_USE_SUGGESTED = "receipt:use_suggested"
CALLBACK_RECEIPT_NONE = "receipt:none"
CALLBACK_CANCEL = "action:cancel"
CALLBACK_QR_GENERATE = "qr:generate"
CALLBACK_QR_SKIP = "qr:skip"
CALLBACK_SUMUP_TERMINAL = "sumup:terminal"
CALLBACK_SUMUP_LINK = "sumup:link"
CALLBACK_CASH_USE_RED = "cash:use_red"
CALLBACK_CASH_USE_BLUE = "cash:use_blue"
CALLBACK_TRANSFER_BLUE_TO_RED = "transfer:blue_to_red"
CALLBACK_TRANSFER_RED_TO_BLUE = "transfer:red_to_blue"



def get_now_keyboard() -> InlineKeyboardMarkup:
    """Tastiera per impostare la data e ora attuale"""
    buttons = [
        [InlineKeyboardButton("📅 Adesso", callback_data=CALLBACK_NOW)],
        [InlineKeyboardButton("❌ Annulla operazione", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_method_keyboard() -> InlineKeyboardMarkup:
    """Tastiera per selezionare Contanti, Satispay o SumUp"""
    buttons = [
        [
            InlineKeyboardButton("💵 Contanti", callback_data=CALLBACK_METHOD_CASH),
            InlineKeyboardButton("📱 Satispay", callback_data=CALLBACK_METHOD_SATISPAY),
            InlineKeyboardButton("💳 SumUp", callback_data=CALLBACK_METHOD_SUMUP)
        ],
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_box_balances_keyboard(red: float, blue: float) -> InlineKeyboardMarkup:
    """Tastiera per confermare gli ultimi saldi rilevati di Cassa Rossa e Cassa Blu"""
    buttons = [
        [InlineKeyboardButton(f"✅ Conferma saldi (🔴 {red:.2f} € · 🔵 {blue:.2f} €)", callback_data=CALLBACK_BOX_USE_LAST)],
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_flow_keyboard() -> InlineKeyboardMarkup:
    """Tastiera per flusso di cassa Entrata o Uscita"""
    buttons = [
        [
            InlineKeyboardButton("➕ Entrata", callback_data=CALLBACK_FLOW_IN),
            InlineKeyboardButton("➖ Uscita", callback_data=CALLBACK_FLOW_OUT)
        ],
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_box_updated_keyboard(calculated_money: float, box: str = "blue") -> InlineKeyboardMarkup:
    """Tastiera per confermare il saldo calcolato, con il pulsante per cambiare la cassa usata (Blu/Rossa)"""
    if box == "red":
        switch_button = InlineKeyboardButton("🔵 Usa Cassa Blu", callback_data=CALLBACK_CASH_USE_BLUE)
    else:
        switch_button = InlineKeyboardButton("🔴 Usa Cassa Rossa", callback_data=CALLBACK_CASH_USE_RED)
    buttons = [
        [InlineKeyboardButton(f"✅ Conferma {calculated_money:.2f} €", callback_data=CALLBACK_BOX_CONFIRM_CALCULATED)],
        [switch_button],
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_receipt_keyboard(suggested_receipt: int) -> InlineKeyboardMarkup:
    """Tastiera per confermare la ricevuta progressiva successiva"""
    buttons = [
        [InlineKeyboardButton(f"✅ Ricevuta #{suggested_receipt}", callback_data=CALLBACK_RECEIPT_USE_SUGGESTED)],
        [InlineKeyboardButton("🚫 Nessuna ricevuta (0)", callback_data=CALLBACK_RECEIPT_NONE)],
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Tastiera con solo pulsante annulla"""
    buttons = [
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_qr_ask_keyboard() -> InlineKeyboardMarkup:
    """Tastiera per chiedere se generare il QR Code Satispay"""
    buttons = [
        [InlineKeyboardButton("📱 Genera QR Code", callback_data=CALLBACK_QR_GENERATE)],
        [InlineKeyboardButton("⏩ Salta e registra subito", callback_data=CALLBACK_QR_SKIP)],
        [InlineKeyboardButton("❌ Annulla operazione", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)


def get_qr_waiting_keyboard() -> InlineKeyboardMarkup:
    """Tastiera durante l'attesa del pagamento del QR Code"""
    buttons = [
        [InlineKeyboardButton("⏩ Salta pagamento e registra", callback_data=CALLBACK_QR_SKIP)],
        [InlineKeyboardButton("❌ Annulla operazione", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)



def get_sumup_ask_keyboard(qr_enabled: bool = True) -> InlineKeyboardMarkup:
    """Tastiera per scegliere come incassare con SumUp (terminale o link/QR se abilitato)"""
    buttons = [[InlineKeyboardButton("💳 Attendi pagamento su terminale", callback_data=CALLBACK_SUMUP_TERMINAL)]]
    if qr_enabled:
        buttons.append([InlineKeyboardButton("🔗 Genera QR link di pagamento", callback_data=CALLBACK_SUMUP_LINK)])
    buttons.append([InlineKeyboardButton("⏩ Salta e registra subito", callback_data=CALLBACK_QR_SKIP)])
    buttons.append([InlineKeyboardButton("❌ Annulla operazione", callback_data=CALLBACK_CANCEL)])
    return InlineKeyboardMarkup(buttons)


def get_transfer_direction_keyboard() -> InlineKeyboardMarkup:
    """Tastiera per scegliere la direzione del trasferimento tra le casse"""
    buttons = [
        [InlineKeyboardButton("🔵 Blu → 🔴 Rossa", callback_data=CALLBACK_TRANSFER_BLUE_TO_RED)],
        [InlineKeyboardButton("🔴 Rossa → 🔵 Blu", callback_data=CALLBACK_TRANSFER_RED_TO_BLUE)],
        [InlineKeyboardButton("❌ Annulla", callback_data=CALLBACK_CANCEL)]
    ]
    return InlineKeyboardMarkup(buttons)
