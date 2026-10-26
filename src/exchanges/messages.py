"""
Order result messages (Telegram / Discord / web), shared by every exchange.

They only read the common order result keys (see src/exchanges/base.py), so an
exchange adapter can use them as is or add its own lines after them.
"""


def format_market_order_message(order_result: dict) -> str:
    """
    Format kết quả đặt lệnh market thành message dễ đọc.
    """
    if not order_result.get('success'):
        error = order_result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    symbol = order_result.get('symbol', 'N/A')
    order_id = order_result.get('order_id', 'N/A')
    entry_price = order_result.get('entry_price') or 0
    stop_loss = order_result.get('stop_loss') or 0
    take_profit = order_result.get('take_profit') or 0
    
    slippage_info = order_result.get('slippage_info', {})
    slippage_pct = slippage_info.get('slippage_pct', 0)
    expected_entry = slippage_info.get('expected_entry', entry_price)
    
    position_info = order_result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = order_result.get('contracts', position_info.get('quantity', 0))
    
    # Tính RR thực tế từ Entry/SL/TP đặt trên sàn
    actual_rr_ratio = 0
    if side == 'BUY':
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0 and reward > 0:
        actual_rr_ratio = reward / risk
    
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    slippage_line = ""
    if slippage_pct != 0:
        slippage_emoji = "ℹ️"
        slippage_label = "không xác định"

        if side == 'BUY':
            if entry_price < expected_entry:
                slippage_emoji = "✅"
                slippage_label = "có lợi"
            elif entry_price > expected_entry:
                slippage_emoji = "⚠️"
                slippage_label = "bất lợi"
            else:
                slippage_label = "trung tính"
        elif side == 'SELL':
            if entry_price > expected_entry:
                slippage_emoji = "✅"
                slippage_label = "có lợi"
            elif entry_price < expected_entry:
                slippage_emoji = "⚠️"
                slippage_label = "bất lợi"
            else:
                slippage_label = "trung tính"

        slippage_line = f"\n├─ Slippage: {slippage_emoji} {slippage_pct:+.3f}% ({slippage_label})"
    
    actual_risk = order_result.get('actual_risk', 0)
    expected_profit = order_result.get('expected_profit', 0)
    expected_risk = position_info.get('expected_risk', actual_risk)
    
    # So sánh actual risk với expected risk
    risk_diff_pct = 0
    if expected_risk > 0:
        risk_diff_pct = ((actual_risk - expected_risk) / expected_risk) * 100
    
    risk_warning = ""
    if abs(risk_diff_pct) > 5:  # Nếu risk chênh > 5%
        risk_warning = f" ⚠️ ({risk_diff_pct:+.1f}% so với cấu hình)"
    
    timing = order_result.get('timing', {})
    timing_line = ""
    if timing:
        order_exec = timing.get('order_execution_seconds', 0)
        signal_to_order = timing.get('signal_to_order_seconds')
        
        if signal_to_order is not None:
            timing_line = f"\n│\n├─ ⏱️ Signal → Order: {signal_to_order:.2f}s"
            timing_line += f"\n├─ ⏱️ Order Execution: {order_exec:.2f}s"
        else:
            timing_line = f"\n│\n├─ ⏱️ Order Execution: {order_exec:.2f}s"
    
    sl_tp_warning = order_result.get('sl_tp_warning')
    header = "✅ THÀNH CÔNG"
    if sl_tp_warning:
        header += f"\n⚠️ CHƯA ĐẶT ĐƯỢC SL/TP TRÊN SÀN: {sl_tp_warning}"

    message = f"""{header}

{side_emoji} {side_text} {symbol}
├─ Order ID: {order_id}
├─ Leverage: {leverage}x
├─ Contracts: {contracts}
│
├─ Expected Entry: ${expected_entry:,.4f}
├─ Actual Entry:   ${entry_price:,.4f}{slippage_line}
│
├─ SL:     ${stop_loss:,.4f}
├─ TP:     ${take_profit:,.4f}
│
├─ Risk:   ${actual_risk:.2f}{risk_warning}
├─ Profit: ${expected_profit:.2f}
└─ RR:     1:{actual_rr_ratio:.2f}{timing_line}"""
    
    return message


def format_limit_order_message(order_result: dict) -> str:
    """
    Format kết quả đặt lệnh LIMIT thành message dễ đọc.
    """
    if not order_result.get('success'):
        error = order_result.get('error', 'Unknown error')
        return f"❌ THẤT BẠI\n└─ Lỗi: {error}"
    
    symbol = order_result.get('symbol', 'N/A')
    entry_price = order_result.get('entry_price') or 0
    current_price = order_result.get('current_price') or 0
    stop_loss = order_result.get('stop_loss') or 0
    take_profit = order_result.get('take_profit') or 0
    order_id = order_result.get('order_id', 'N/A')
    order_status = order_result.get('order_status') or 'open'
    time_in_force = order_result.get('time_in_force') or 'GTC'
    
    position_info = order_result.get('position_info', {})
    side = position_info.get('side', 'N/A').upper()
    leverage = position_info.get('leverage', 'N/A')
    contracts = order_result.get('contracts', position_info.get('quantity', 0))
    
    # Tính RR thực tế từ entry, SL, TP
    actual_rr_ratio = 0
    if side == 'BUY':
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(take_profit - entry_price) if take_profit and entry_price else 0
    else:
        risk = abs(stop_loss - entry_price) if stop_loss and entry_price else 0
        reward = abs(entry_price - take_profit) if entry_price and take_profit else 0
    
    if risk > 0 and reward > 0:
        actual_rr_ratio = reward / risk
    
    side_emoji = "🟢" if side == "BUY" else "🔴"
    side_text = "LONG" if side == "BUY" else "SHORT"
    
    price_diff_pct = 0
    if current_price > 0:
        price_diff_pct = ((entry_price - current_price) / current_price) * 100
    
    expected_risk = order_result.get('expected_risk', 0)
    expected_profit = order_result.get('expected_profit', 0)
    
    timing = order_result.get('timing', {})
    timing_line = ""
    if timing:
        order_exec = timing.get('order_execution_seconds', 0)
        signal_to_order = timing.get('signal_to_order_seconds')
        
        if signal_to_order is not None:
            timing_line = f"\n│\n├─ ⏱️ Signal → Order: {signal_to_order:.2f}s"
            timing_line += f"\n├─ ⏱️ Order Placed: {order_exec:.2f}s"
        else:
            timing_line = f"\n│\n├─ ⏱️ Order Placed: {order_exec:.2f}s"
    
    price_warning = order_result.get('price_warning', '')
    warning_line = ""
    if price_warning:
        warning_line = f"\n├─ ⚠️ {price_warning}"
    
    message = f"""📝 LIMIT ORDER PLACED

{side_emoji} {side_text} {symbol}
├─ Order ID: {order_id}
├─ Status: {order_status.upper()}
├─ Time in Force: {time_in_force}
├─ Leverage: {leverage}x
├─ Contracts: {contracts}
│
├─ Limit Price:   ${entry_price:,.4f}
├─ Current Price: ${current_price:,.4f} ({price_diff_pct:+.2f}%){warning_line}
│
├─ SL:     ${stop_loss:,.4f}
├─ TP:     ${take_profit:,.4f}
│
├─ Risk:   ${expected_risk:.2f}
├─ Profit: ${expected_profit:.2f}
└─ RR:     1:{actual_rr_ratio:.2f}{timing_line}"""
    
    return message
