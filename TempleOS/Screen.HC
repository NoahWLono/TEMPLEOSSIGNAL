// Native 16-color graphics interface. Original code, GPL-3.0.
// No remote text ever enters a DolDoc parser or a format string.

#define TS_TEXT_MAX 1024
#define TS_PEER_MAX 128
#define TS_NAME_MAX 128
#define TS_HISTORY 256
#define TS_COLUMNS 54
#define TS_PEOPLE_MAX 26

U8 ts_to[TS_PEER_MAX+1]="self",ts_kind[16]="S";
U8 ts_composer[TS_TEXT_MAX+1];
I64 ts_composer_len=0;
U8 ts_status[160]="Connecting to the local serial bridge...";
U8 ts_backend[16]="OFFLINE";
Bool ts_connected=FALSE;
I64 ts_focus=1,ts_picker=0,ts_selected=0;
I64 ts_people_count=0,ts_offset=0,ts_next_offset=-1;
U8 ts_people_id[TS_PEOPLE_MAX][TS_PEER_MAX+1];
U8 ts_people_name[TS_PEOPLE_MAX][TS_NAME_MAX+1];
U8 ts_lines[TS_HISTORY][TS_COLUMNS+1];
I64 ts_line_color[TS_HISTORY];
I64 ts_line_count=0,ts_scroll=0;

U0 TSCopy(U8 *dst,U8 *src,I64 capacity)
{
  I64 i=0;
  if (capacity<=0)
    return;
  while (i<capacity-1 && src[i]) {
    dst[i]=src[i];
    i++;
  }
  dst[i]=0;
}

U0 TSPlain(CDC *dc,I64 x,I64 y,U8 *text,I64 max_chars=78)
{
  I64 i=0,n=0,ch;
  // Individual glyphs avoid even GrPrint's $$SX/SY directives.
  while (text[i] && n<max_chars) {
    ch=text[i++];
    if (ch>=0x80) {
      while ((text[i]&0xC0)==0x80)
        i++;
      ch='?';
    }
    if (ch<32 || ch==127)
      ch=' ';
    GrPutChar(dc,x+n*8,y,ch);
    n++;
  }
}

U0 TSLine(U8 *text,I64 color=BLACK)
{
  I64 slot=ts_line_count%TS_HISTORY;
  TSCopy(ts_lines[slot],text,TS_COLUMNS+1);
  ts_line_color[slot]=color;
  ts_line_count++;
  ts_scroll=0;
}

U0 TSLog(U8 *who,U8 *text,I64 color=BLACK)
{
  I64 i=0,n=0,ch;
  U8 line[TS_COLUMNS+1];
  if (who && *who)
    TSLine(who,color);
  while (text[i]) {
    ch=text[i++];
    if (ch>=0x80) {
      while ((text[i]&0xC0)==0x80)
        i++;
      ch='?';
    }
    if (ch=='\n' || ch=='\r' || n==TS_COLUMNS) {
      line[n]=0;
      TSLine(line,BLACK);
      n=0;
      if (ch=='\r' && text[i]=='\n')
        i++;
      if (ch=='\n' || ch=='\r')
        goto ts_log_next;
    }
    if (ch<32 || ch==127)
      ch=' ';
    line[n++]=ch;
ts_log_next:
    ;
  }
  if (n || !*text) {
    line[n]=0;
    TSLine(line,BLACK);
  }
  TSLine("",BLACK);
}

U0 TSSetStatus(U8 *text)
{
  TSCopy(ts_status,text,sizeof(ts_status));
}

U0 TSDraw(CTask *,CDC *dc)
{
  I64 i,row,start,slot,first,j;
  U8 buf[96];
  dc->color=WHITE;
  GrRect(dc,0,0,640,480);
  dc->color=BLUE;
  GrRect(dc,0,0,640,40);
  dc->color=WHITE;
  GrCircle(dc,20,19,12);
  GrCircle(dc,20,19,9);
  GrLine(dc,10,28,7,33);
  GrLine(dc,7,33,17,29);
  TSPlain(dc,42,9,"TEMPLEOS SIGNAL");
  TSPlain(dc,42,24,"HOLYC / 640x480 / 16 COLORS / SERIAL");
  dc->color=YELLOW;
  TSPlain(dc,536,10,ts_backend,12);
  dc->color=LTGRAY;
  GrRect(dc,0,40,176,326);
  dc->color=DKGRAY;
  GrLine(dc,176,40,176,366);
  dc->color=BLUE;
  if (ts_picker==1)
    TSPlain(dc,8,51,"CONTACTS",20);
  else if (ts_picker==2)
    TSPlain(dc,8,51,"GROUPS",20);
  else
    TSPlain(dc,8,51,"LOCAL BRIDGE",20);
  dc->color=BLACK;
  if (ts_picker) {
    for (i=0;i<ts_people_count;i++) {
      row=70+i*10;
      if (i==ts_selected) {
        dc->color=BLUE;
        GrRect(dc,3,row-1,170,10);
        dc->color=WHITE;
      } else
        dc->color=BLACK;
      TSPlain(dc,8,row,ts_people_name[i],20);
    }
    if (!ts_people_count)
      TSPlain(dc,8,73,"No entries loaded.",20);
    dc->color=DKGRAY;
    TSPlain(dc,8,340,"PgUp/PgDn: pages",20);
    TSPlain(dc,8,352,"Enter: use target",20);
  } else {
    TSPlain(dc,8,73,"F2  Contacts",20);
    TSPlain(dc,8,91,"F3  Groups",20);
    TSPlain(dc,8,109,"F4  Note to self",20);
    TSPlain(dc,8,127,"F5  Reconnect",20);
    TSPlain(dc,8,145,"F6  Clear screen",20);
    TSPlain(dc,8,171,"Tab: target / text",20);
    TSPlain(dc,8,189,"Enter: send",20);
    TSPlain(dc,8,207,"PgUp/PgDn: history",20);
    dc->color=DKGRAY;
    TSPlain(dc,8,251,"Signal keys stay",20);
    TSPlain(dc,8,263,"on your host.",20);
    TSPlain(dc,8,287,"Serial carries",20);
    TSPlain(dc,8,299,"plaintext locally.",20);
    TSPlain(dc,8,323,"Text-only client.",20);
    TSPlain(dc,8,335,"No Unicode font.",20);
    TSPlain(dc,8,347,"No disk history.",20);
  }
  dc->color=BLUE;
  TSPlain(dc,184,51,"CONVERSATION ACTIVITY",TS_COLUMNS);
  dc->color=LTGRAY;
  GrLine(dc,184,64,631,64);
  start=ts_line_count-28-ts_scroll;
  if (start<0)
    start=0;
  if (start<ts_line_count-TS_HISTORY)
    start=ts_line_count-TS_HISTORY;
  row=73;
  for (i=start;i<ts_line_count && i<start+28;i++) {
    slot=i%TS_HISTORY;
    dc->color=ts_line_color[slot];
    TSPlain(dc,184,row,ts_lines[slot],TS_COLUMNS);
    row+=10;
  }
  dc->color=BLUE;
  GrRect(dc,0,366,640,18);
  dc->color=WHITE;
  TSPlain(dc,8,371,ts_status,78);
  dc->color=LTGRAY;
  if (ts_focus==0)
    dc->color=YELLOW;
  GrRect(dc,0,384,640,20);
  dc->color=BLACK;
  TSPlain(dc,8,391,"To:");
  TSPlain(dc,40,391,ts_to,64);
  dc->color=BLUE;
  TSPlain(dc,568,391,ts_kind,8);
  dc->color=LTGRAY;
  if (ts_focus==1)
    dc->color=YELLOW;
  GrRect(dc,0,405,640,45);
  dc->color=BLACK;
  first=0;
  if (ts_composer_len>228)
    first=((ts_composer_len-1)/76-2)*76;
  for (i=0;i<3;i++) {
    j=0;
    while (j<76 && first+i*76+j<ts_composer_len) {
      buf[j]=ts_composer[first+i*76+j];
      j++;
    }
    buf[j]=0;
    TSPlain(dc,8,410+i*12,buf,76);
  }
  if (ts_focus==1 && !ts_picker) {
    i=ts_composer_len-first;
    if (i>=0 && i<228) {
      dc->color=BLUE;
      GrRect(dc,8+(i%76)*8,418+(i/76)*12,7,1);
    }
  }
  dc->color=DKGRAY;
  StrPrint(buf,"%d/%d ASCII bytes  |  Esc: quit  |  No automatic send retries",
        ts_composer_len,TS_TEXT_MAX);
  TSPlain(dc,8,458,buf,78);
}
