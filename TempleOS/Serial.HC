// TEMPLEOSSIGNAL: original HolyC code, GPL-3.0.
// Polled 16550 UART. No TempleOS IP stack, interrupts, or kernel patches.
// Register meanings checked against TempleOS's public-domain Doc/Comm.HC.

I64 ts_uart_base=0x3F8;
I64 ts_uart_old_lcr,ts_uart_old_ier,ts_uart_old_mcr;
I64 ts_uart_old_dll,ts_uart_old_dlm;
I64 ts_uart_errors=0;
Bool ts_uart_open=FALSE;

Bool TSUartInit(I64 base=0x3F8)
{
  if (base!=0x3F8 && base!=0x2F8 && base!=0x3E8 && base!=0x2E8)
    return FALSE;
  ts_uart_base=base;
  if (InU8(base+5)==0xFF)
    return FALSE;
  ts_uart_old_lcr=InU8(base+3);
  OutU8(base+3,ts_uart_old_lcr&0x7F);
  ts_uart_old_ier=InU8(base+1);
  ts_uart_old_mcr=InU8(base+4);
  OutU8(base+1,0);
  OutU8(base+3,0x80);
  ts_uart_old_dll=InU8(base);
  ts_uart_old_dlm=InU8(base+1);
  OutU8(base,1);              // 115200 baud divisor
  OutU8(base+1,0);
  OutU8(base+3,3);            // 8 data bits, no parity, 1 stop bit
  OutU8(base+2,7);            // Enable and clear the UART FIFOs
  OutU8(base+4,3);            // DTR + RTS; interrupt gate stays disabled
  ts_uart_errors=0;
  ts_uart_open=TRUE;
  return TRUE;
}

U0 TSUartClose()
{
  if (!ts_uart_open)
    return;
  OutU8(ts_uart_base+1,0);
  OutU8(ts_uart_base+3,0x80);
  OutU8(ts_uart_base,ts_uart_old_dll);
  OutU8(ts_uart_base+1,ts_uart_old_dlm);
  OutU8(ts_uart_base+3,ts_uart_old_lcr&0x7F);
  OutU8(ts_uart_base+1,ts_uart_old_ier);
  OutU8(ts_uart_base+4,ts_uart_old_mcr);
  OutU8(ts_uart_base+2,0);    // FIFO state cannot be read back
  OutU8(ts_uart_base+3,ts_uart_old_lcr);
  ts_uart_open=FALSE;
}

I64 TSUartRead()
{
  I64 status;
  if (!ts_uart_open)
    return -1;
  status=InU8(ts_uart_base+5);
  if (status&0x1E) {
    ts_uart_errors++;
    if (status&1)
      InU8(ts_uart_base);
    return -2;               // Caller discards until a fresh newline
  }
  if (status&1)
    return InU8(ts_uart_base);
  return -1;
}

Bool TSUartWrite(U8 *data,I64 length,F64 timeout=5.0)
{
  I64 i;
  F64 deadline=tS+timeout;
  if (!ts_uart_open || length<0)
    return FALSE;
  for (i=0;i<length;i++) {
    while (!(InU8(ts_uart_base+5)&0x20)) {
      if (tS>=deadline)
        return FALSE;
      Yield;
    }
    if (tS>=deadline)
      return FALSE;
    OutU8(ts_uart_base,data[i]);
  }
  return TRUE;
}

U0 TSDebug(U8 *text)
{
  // QEMU: -debugcon file:smoke.log -global isa-debugcon.iobase=0xe9
  // No application or message contents should be sent here.
  while (*text)
    OutU8(0xE9,*text++);
}
