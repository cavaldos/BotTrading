"""
Bybit order result formatter for Discord notifications
"""


def format_result(result: dict) -> str:
    """
    Format Bybit result object thành chuỗi dễ đọc cho Discord
    """
    if result is None:
        return ""
    
    if result.get('success', False):
        # Trích xuất thông tin từ order và position
        order = result.get('order', {})
        position_info = result.get('position_info', {})
        
        order_id = order.get('info', {}).get('orderId', 'N/A')
        quantity = position_info.get('quantity', 'N/A')
        margin = position_info.get('margin_required', 'N/A')
        leverage = position_info.get('leverage', 'N/A')
        side = position_info.get('side', 'N/A').upper()
        sl_tp_status = result.get('sl_tp_warning', 'N/A')
        
        return f"""✓ ORDER PLACED
Order ID:   {order_id}
Side:       {side}
Quantity:   {quantity}
Margin:     ${margin}
Leverage:   {leverage}x
SL/TP:      {sl_tp_status}"""
    else:
        # Trích xuất error message
        error = result.get('error', 'Unknown error')
        position_info = result.get('position_info', {})
        
        # Parse error message to get the key part
        if 'bybit' in error:
            error_parts = error.split('"retMsg":"')
            if len(error_parts) > 1:
                error_msg = error_parts[1].split('"')[0]
            else:
                error_msg = error
        else:
            error_msg = error
        
        quantity = position_info.get('quantity', 'N/A')
        
        return f"""✗ ORDER FAILED
Error:      {error_msg}
Quantity:   {quantity}"""
