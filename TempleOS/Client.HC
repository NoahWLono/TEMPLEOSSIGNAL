// Native TempleOS Signal text client. Original HolyC code, GPL-3.0.

U8 ts_last_event[32]="0";
U8 ts_last_text[TS_FIELD_MAX+1];
I64 ts_received=0;

U0 TSOffline(U8 *reason)
{
  ts_connected=FALSE;
  TSCopy(ts_backend,"OFFLINE",sizeof(ts_backend));
  TSSetStatus(reason);
}

Bool TSErrorReply()
{
  if (StrCmp(ts_reply.op,"ERR"))
    return FALSE;
  if (ts_reply.count>=2) {
    TSSetStatus(ts_reply.field[1]);
    TSLog(ts_reply.field[0],ts_reply.field[1],RED);
  } else
    TSSetStatus("Invalid error response. Press F5 to reconnect.");
  return TRUE;
}

Bool TSConnect()
{
  ts_connected=FALSE;
  TSCopy(ts_backend,"OFFLINE",sizeof(ts_backend));
  TSSetStatus("Waiting for bridge HELLO...");
  if (!TSCall("HELLO",0,,,,5.0)) {
    TSOffline("No serial bridge. Start the host bridge, then press F5.");
    return FALSE;
  }
  if (TSErrorReply)
    return FALSE;
  if (StrCmp(ts_reply.op,"READY") || ts_reply.count!=3
      || StrCmp(ts_reply.field[0],"1")
      || StrCmp(ts_reply.field[1],"DEMO") && StrCmp(ts_reply.field[1],"SIGNAL")) {
    TSOffline("Incompatible bridge response.");
    return FALSE;
  }
  TSCopy(ts_backend,ts_reply.field[1],sizeof(ts_backend));
  TSCopy(ts_last_event,"0",sizeof(ts_last_event));
  ts_connected=TRUE;
  if (!StrCmp(ts_backend,"DEMO")) {
    TSSetStatus("DEMO: local simulator only. No Signal account is connected.");
    TSLog("LOCAL DEMO",ts_reply.field[2],PURPLE);
  } else {
    TSSetStatus("SIGNAL host bridge connected. F2 contacts, F3 groups, F4 self.");
    TSLog("SIGNAL BRIDGE",ts_reply.field[2],BLUE);
  }
  return TRUE;
}

Bool TSPoll()
{
  U8 header[TS_COLUMNS+1];
  I64 event_number;
  if (!ts_connected)
    return FALSE;
  if (!TSCall("POLL",1,ts_last_event,,,5.0)) {
    TSOffline("Serial reply timed out. Press F5 to reconnect.");
    return FALSE;
  }
  if (TSErrorReply)
    return FALSE;
  if (!StrCmp(ts_reply.op,"EMPTY") && !ts_reply.count)
    return FALSE;
  if (StrCmp(ts_reply.op,"EVENT") || ts_reply.count!=7
      || !TSDecimal(ts_reply.field[0],&event_number)
      || !event_number || StrLen(ts_reply.field[0])>=sizeof(ts_last_event)) {
    TSOffline("Invalid event ignored. Press F5 to reconnect.");
    return FALSE;
  }
  if (!StrCmp(ts_last_event,ts_reply.field[0]))
    return FALSE;
  if (ts_reply.field[1][0]=='N')
    TSLog("BRIDGE NOTICE",ts_reply.field[6],PURPLE);
  else {
    if (ts_reply.field[1][0]=='G')
      TSLog("GROUP ID",ts_reply.field[2],PURPLE);
    if (ts_reply.field[1][0]=='S')
      TSLine("NOTE TO SELF",PURPLE);
    TSCopy(header,ts_reply.field[4],sizeof(header));
    if (!*header)
      TSCopy(header,ts_reply.field[3],sizeof(header));
    if (!*header)
      TSCopy(header,"Incoming message",sizeof(header));
    TSLog(header,ts_reply.field[6],BLUE);
  }
  TSCopy(ts_last_text,ts_reply.field[6],sizeof(ts_last_text));
  TSCopy(ts_last_event,ts_reply.field[0],sizeof(ts_last_event));
  ts_received++;
  return TRUE;
}

Bool TSSend()
{
  U8 *target;
  if (!ts_connected) {
    TSSetStatus("Bridge offline. Press F5 to connect before sending.");
    return FALSE;
  }
  if (!ts_composer_len) {
    TSSetStatus("Type a message first.");
    return FALSE;
  }
  target=ts_to;
  if (ts_kind[0]=='S')
    target="";
  if (ts_kind[0]!='S' && !*target) {
    TSSetStatus("Enter a recipient or choose F4 for Note to Self.");
    return FALSE;
  }
  TSSetStatus("Sending once. Awaiting the host result...");
  if (!TSCall("SEND",3,ts_kind,target,ts_composer)) {
    TSOffline("Send status UNKNOWN. Check phone before sending again. F5 reconnect.");
    TSLog("SEND STATUS UNKNOWN",
        "No retry was made. Check the recipient or your phone before retrying.",RED);
    return FALSE;
  }
  if (TSErrorReply)
    return FALSE;
  if (StrCmp(ts_reply.op,"SENT") || ts_reply.count!=1) {
    TSSetStatus("Unexpected send response. Check phone before retrying.");
    return FALSE;
  }
  TSLog("You ->",ts_to,BLUE);
  TSLog("Accepted by host",ts_composer,BLUE);
  MemSet(ts_composer,0,sizeof(ts_composer));
  ts_composer_len=0;
  TSSetStatus("Host accepted the message. This is not a delivery/read receipt.");
  return TRUE;
}

U0 TSLoadPeople(I64 kind,I64 offset=0)
{
  U8 offset_text[16],*op,*expected;
  I64 next,count=0;
  if (!ts_connected) {
    TSSetStatus("Press F5 to connect before loading contacts or groups.");
    return;
  }
  ts_picker=kind;
  ts_people_count=0;
  ts_selected=0;
  ts_offset=offset;
  ts_next_offset=-1;
  if (kind==1) {
    op="CONTACTS";
    expected="CONTACT";
  } else {
    op="GROUPS";
    expected="GROUP";
  }
  TSSetStatus("Loading a page from the Signal host...");
  while (count<TS_PEOPLE_MAX) {
    StrPrint(offset_text,"%d",offset);
    if (!TSCall(op,1,offset_text)) {
      TSOffline("Contact/group reply timed out. Press F5 to reconnect.");
      return;
    }
    if (TSErrorReply)
      return;
    if (!StrCmp(ts_reply.op,"END") && !ts_reply.count)
      break;
    if (StrCmp(ts_reply.op,expected) || ts_reply.count!=3
        || !TSDecimal(ts_reply.field[0],&next) || next<=offset
        || StrLen(ts_reply.field[1])>TS_PEER_MAX
        || StrLen(ts_reply.field[2])>TS_NAME_MAX) {
      TSSetStatus("Invalid contact/group response ignored.");
      return;
    }
    TSCopy(ts_people_id[count],ts_reply.field[1],TS_PEER_MAX+1);
    TSCopy(ts_people_name[count],ts_reply.field[2],TS_NAME_MAX+1);
    if (!ts_people_name[count][0])
      TSCopy(ts_people_name[count],ts_people_id[count],TS_NAME_MAX+1);
    offset=next;
    ts_people_count=++count;
  }
  if (count==TS_PEOPLE_MAX)
    ts_next_offset=offset;
  TSSetStatus("Up/Down selects; Enter chooses; PgUp/PgDn changes pages; Esc closes.");
}

U0 TSChoosePerson()
{
  if (ts_selected<0 || ts_selected>=ts_people_count)
    return;
  TSCopy(ts_to,ts_people_id[ts_selected],sizeof(ts_to));
  if (ts_picker==2)
    TSCopy(ts_kind,"G",sizeof(ts_kind));
  else
    TSCopy(ts_kind,"D",sizeof(ts_kind));
  ts_picker=0;
  ts_focus=1;
  TSSetStatus("Recipient selected. Type a message and press Enter.");
}

U0 TSClearScreen()
{
  MemSet(ts_lines,0,sizeof(ts_lines));
  MemSet(ts_line_color,0,sizeof(ts_line_color));
  MemSet(ts_last_text,0,sizeof(ts_last_text));
  ts_line_count=0;
  ts_scroll=0;
  TSSetStatus("Visible transcript cleared from this application buffer.");
}

Bool TSKey(I64 ch,I64 sc)
{
  I64 n;
  if (ch==CH_ESC || ch==CH_SHIFT_ESC) {
    if (ts_picker) {
      ts_picker=0;
      return TRUE;
    }
    return FALSE;
  }
  switch (sc&255) {
    case SC_F2: TSLoadPeople(1); return TRUE;
    case SC_F3: TSLoadPeople(2); return TRUE;
    case SC_F4:
      TSCopy(ts_kind,"S",sizeof(ts_kind));
      TSCopy(ts_to,"self",sizeof(ts_to));
      ts_focus=1; ts_picker=0;
      TSSetStatus("Note to Self selected.");
      return TRUE;
    case SC_F5: TSConnect; return TRUE;
    case SC_F6: TSClearScreen; return TRUE;
    case SC_PAGE_UP:
      if (ts_picker)
        TSLoadPeople(ts_picker,MaxI64(0,ts_offset-TS_PEOPLE_MAX));
      else
        ts_scroll=MinI64(MaxI64(0,MinI64(ts_line_count,TS_HISTORY)-28),ts_scroll+20);
      return TRUE;
    case SC_PAGE_DOWN:
      if (ts_picker) {
        if (ts_next_offset>=0)
          TSLoadPeople(ts_picker,ts_next_offset);
      } else
        ts_scroll=MaxI64(0,ts_scroll-20);
      return TRUE;
    case SC_CURSOR_UP:
      if (ts_picker)
        ts_selected=MaxI64(0,ts_selected-1);
      return TRUE;
    case SC_CURSOR_DOWN:
      if (ts_picker)
        ts_selected=MinI64(ts_people_count-1,ts_selected+1);
      return TRUE;
  }
  if (ts_picker) {
    if (ch=='\n' || ch=='\r')
      TSChoosePerson;
    return TRUE;
  }
  if (ch=='\t') {
    ts_focus=1-ts_focus;
    return TRUE;
  }
  if (ch=='\n' || ch=='\r') {
    if (!ts_focus)
      ts_focus=1;
    else
      TSSend;
    return TRUE;
  }
  if (ch==CH_CTRLU) {
    if (ts_focus) {
      MemSet(ts_composer,0,sizeof(ts_composer));
      ts_composer_len=0;
    } else {
      MemSet(ts_to,0,sizeof(ts_to));
      TSCopy(ts_kind,"D",sizeof(ts_kind));
    }
    return TRUE;
  }
  if (ch==CH_BACKSPACE) {
    if (ts_focus && ts_composer_len)
      ts_composer[--ts_composer_len]=0;
    else if (!ts_focus) {
      n=StrLen(ts_to);
      if (n)
        ts_to[n-1]=0;
      if (ts_kind[0]=='S')
        TSCopy(ts_kind,"D",sizeof(ts_kind));
    }
    return TRUE;
  }
  if (ch>=32 && ch<=126 && !(sc&(SCF_CTRL|SCF_ALT))) {
    if (ts_focus) {
      if (ts_composer_len<TS_TEXT_MAX) {
        ts_composer[ts_composer_len++]=ch;
        ts_composer[ts_composer_len]=0;
      }
    } else {
      if (ts_kind[0]=='S') {
        ts_to[0]=0;
        TSCopy(ts_kind,"D",sizeof(ts_kind));
      }
      n=StrLen(ts_to);
      if (n<TS_PEER_MAX) {
        ts_to[n++]=ch;
        ts_to[n]=0;
      }
    }
  }
  return TRUE;
}

U0 TSSignal()
{
  I64 ch,sc;
  F64 next_poll;
  Bool running=TRUE,failed=FALSE;
  SettingsPush;
  AutoComplete;
  WinBorder;
  WinMax;
  DocCursor;
  DocClear;
  Fs->draw_it=&TSDraw;
  try {
    if (TSUartInit)
      TSConnect;
    else
      TSOffline("COM1 UART is missing. Check the QEMU serial configuration.");
    next_poll=tS;
    while (running) {
      while (ScanKey(&ch,&sc))
        if (!TSKey(ch,sc)) {
          running=FALSE;
          break;
        }
      if (running && ts_connected && tS>=next_poll) {
        if (TSPoll)
          next_poll=tS+0.02;
        else
          next_poll=tS+0.30;
      }
      Sleep(10);
    }
  } catch {
    Fs->catch_except=TRUE;
    failed=TRUE;
  }
  Fs->draw_it=NULL;
  TSUartClose;
  // Application-buffer clearing is not a secure-erasure guarantee.
  MemSet(ts_composer,0,sizeof(ts_composer));
  MemSet(ts_lines,0,sizeof(ts_lines));
  MemSet(ts_people_id,0,sizeof(ts_people_id));
  MemSet(ts_people_name,0,sizeof(ts_people_name));
  MemSet(&ts_reply,0,sizeof(ts_reply));
  MemSet(ts_rx,0,sizeof(ts_rx));
  MemSet(ts_tx,0,sizeof(ts_tx));
  MemSet(ts_last_text,0,sizeof(ts_last_text));
  ts_composer_len=0;
  ts_line_count=0;
  SettingsPop;
  DocClear;
  if (failed)
    Print("TEMPLEOSSIGNAL stopped after a runtime exception.\n");
}
