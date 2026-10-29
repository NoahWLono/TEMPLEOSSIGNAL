// HolyC tests run by the real TempleOS compiler. No test sends to Signal.
// Include Library.HC first. Results go to QEMU's debug console (port 0xE9).

Bool TSSelfTest()
{
  I64 length,value,i;
  U8 wire[TS_LINE_MAX+1],copy[TS_LINE_MAX+1];
  U8 decoded[TS_FIELD_MAX+1],large[TS_FIELD_MAX+1];
  U8 invalid_utf8[3];
  U8 *fields[3];
  CTSFrame frame;
  invalid_utf8[0]=0xC0;
  invalid_utf8[1]=0xAF;
  invalid_utf8[2]=0;
  if (TSCRC("123456789",9)!=0x29B1)
    return FALSE;
  length=TSBuild(wire,1,"PING",0,NULL);
  if (length!=16 || StrCmp(wire,"TS1\t1\tPING\t8397\n"))
    return FALSE;
  TSCopy(copy,wire,sizeof(copy));
  if (!TSParse(copy,length-1,&frame) || frame.id!=1
      || StrCmp(frame.op,"PING") || frame.count)
    return FALSE;
  TSCopy(copy,wire,sizeof(copy));
  copy[8]='X';
  if (TSParse(copy,length-1,&frame))
    return FALSE;
  if (!TSDecode("48656C6C6F",decoded) || StrCmp(decoded,"Hello"))
    return FALSE;
  if (!TSDecode("-",decoded) || *decoded)
    return FALSE;
  if (TSDecode("F",decoded) || TSDecode("GG",decoded)
      || TSDecode("00",decoded) || TSDecode("C0AF",decoded)
      || TSDecode("eda080",decoded))
    return FALSE;
  if (TSUtf8(invalid_utf8,2) || !TSUtf8("Hello",5))
    return FALSE;
  if (!TSDecimal("2147483647",&value) || value!=2147483647
      || TSDecimal("2147483648",&value) || TSDecimal("-1",&value)
      || TSDecimal("",&value) || TSDecimal("1x",&value))
    return FALSE;
  fields[0]="S"; fields[1]=""; fields[2]="Hello";
  length=TSBuild(wire,2,"SEND",3,fields);
  if (StrCmp(wire,"TS1\t2\tSEND\t53\t-\t48656C6C6F\tEB31\n"))
    return FALSE;
  TSCopy(copy,wire,sizeof(copy));
  if (!TSParse(copy,length-1,&frame) || frame.count!=3
      || StrCmp(frame.field[0],"S") || *frame.field[1]
      || StrCmp(frame.field[2],"Hello"))
    return FALSE;
  for (i=0;i<TS_FIELD_MAX;i++)
    large[i]='A';
  large[TS_FIELD_MAX]=0;
  fields[0]=large; fields[1]=large;
  if (TSBuild(wire,3,"SEND",2,fields)!=-1
      || TSBuild(wire,0,"PING",0,NULL)!=-1
      || TSBuild(wire,1,"bad",0,NULL)!=-1)
    return FALSE;
  TSDebug("TS SELFTEST PASS\n");
  return TRUE;
}

Bool TSSmoke()
{
  Bool ok=TRUE,received=FALSE;
  I64 i;
  TSDebug("TS SELFTEST START\n");
  if (!TSSelfTest) {
    TSDebug("TS SELFTEST FAIL\nTS SMOKE FAIL\n");
    return FALSE;
  }
  if (!TSUartInit) {
    TSDebug("TS SERIAL FAIL\nTS SMOKE FAIL\n");
    return FALSE;
  }
  if (!TSConnect || StrCmp(ts_backend,"DEMO")) {
    TSDebug("TS SMOKE REQUIRES DEMO\n");
    ok=FALSE;
  }
  if (ok && (!TSCall("PING",0,,,,5.0) || StrCmp(ts_reply.op,"PONG")
      || ts_reply.count))
    ok=FALSE;
  if (ok)
    TSDebug("TS SERIAL PASS\n");
  if (ok && (!TSCall("CONTACTS",1,"0") || StrCmp(ts_reply.op,"CONTACT")
      || ts_reply.count!=3))
    ok=FALSE;
  if (ok && (!TSCall("GROUPS",1,"0") || StrCmp(ts_reply.op,"GROUP")
      || ts_reply.count!=3))
    ok=FALSE;
  if (ok) {
    TSCopy(ts_to,"self",sizeof(ts_to));
    TSCopy(ts_kind,"S",sizeof(ts_kind));
    TSCopy(ts_composer,"TempleOS smoke test",sizeof(ts_composer));
    ts_composer_len=StrLen(ts_composer);
    if (!TSSend)
      ok=FALSE;
  }
  if (ok) {
    for (i=0;i<8 && !received;i++) {
      if (TSPoll && !StrCmp(ts_last_text,"TempleOS smoke test"))
        received=TRUE;
      Sleep(20);
    }
    if (!received)
      ok=FALSE;
  }
  if (ok && (!TSCall("POLL",1,ts_last_event) || StrCmp(ts_reply.op,"EMPTY")))
    ok=FALSE;
  TSUartClose;
  if (ok)
    TSDebug("TS SMOKE PASS\n");
  else
    TSDebug("TS SMOKE FAIL\n");
  return ok;
}
