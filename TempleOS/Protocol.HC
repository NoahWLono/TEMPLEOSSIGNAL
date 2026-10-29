// Bounded TS1 serial framing. Original code, GPL-3.0.
// See ../PROTOCOL.md. CRC detects corruption; it is not authentication.

#define TS_LINE_MAX 4096
#define TS_FIELDS_MAX 10
#define TS_FIELD_MAX 1536

class CTSFrame
{
  I64 id,count;
  U8 op[17];
  U8 field[TS_FIELDS_MAX][TS_FIELD_MAX+1];
};

I64 ts_request_id=0,ts_bad_frames=0,ts_rx_count=0;
Bool ts_rx_drop=FALSE;
U8 ts_rx[TS_LINE_MAX+1],ts_tx[TS_LINE_MAX+1];
CTSFrame ts_reply;

I64 TSCRC(U8 *data,I64 length)
{
  I64 crc=0xFFFF,i,j;
  for (i=0;i<length;i++) {
    crc^=data[i]<<8;
    for (j=0;j<8;j++) {
      if (crc&0x8000)
        crc=(crc<<1)^0x1021;
      else
        crc<<=1;
      crc&=0xFFFF;
    }
  }
  return crc;
}

I64 TSHexDigit(I64 ch)
{
  if (ch>='0' && ch<='9')
    return ch-'0';
  if (ch>='A' && ch<='F')
    return ch-'A'+10;
  return -1;
}

Bool TSDecimal(U8 *s,I64 *value)
{
  I64 n=0,i=0;
  if (!*s)
    return FALSE;
  while (s[i]) {
    if (s[i]<'0' || s[i]>'9' || i>=10)
      return FALSE;
    n=n*10+s[i]-'0';
    if (n>2147483647)
      return FALSE;
    i++;
  }
  *value=n;
  return TRUE;
}

Bool TSUtf8(U8 *s,I64 length)
{
  I64 i=0,ch,need,code,min;
  while (i<length) {
    ch=s[i++];
    if (!ch)
      return FALSE;
    if (ch<0x80)
      goto ts_utf8_next;
    if (ch>=0xC2 && ch<=0xDF) {
      need=1; code=ch&31; min=0x80;
    } else if (ch>=0xE0 && ch<=0xEF) {
      need=2; code=ch&15; min=0x800;
    } else if (ch>=0xF0 && ch<=0xF4) {
      need=3; code=ch&7; min=0x10000;
    } else
      return FALSE;
    if (i+need>length)
      return FALSE;
    while (need--) {
      ch=s[i++];
      if ((ch&0xC0)!=0x80)
        return FALSE;
      code=(code<<6)|(ch&63);
    }
    if (code<min || code>0x10FFFF || code>=0xD800 && code<=0xDFFF)
      return FALSE;
ts_utf8_next:
    ;
  }
  return TRUE;
}

Bool TSOpcode(U8 *s)
{
  I64 i=0;
  if (!*s)
    return FALSE;
  while (s[i]) {
    if (i>=16 || (s[i]<'A' || s[i]>'Z') && s[i]!='_')
      return FALSE;
    i++;
  }
  return TRUE;
}

Bool TSDecode(U8 *src,U8 *dst)
{
  I64 n=StrLen(src),i,a,b;
  if (n==1 && src[0]=='-') {
    dst[0]=0;
    return TRUE;
  }
  if (!n || n&1 || n>TS_FIELD_MAX*2)
    return FALSE;
  for (i=0;i<n;i+=2) {
    a=TSHexDigit(src[i]);
    b=TSHexDigit(src[i+1]);
    if (a<0 || b<0)
      return FALSE;
    dst[i/2]=(a<<4)|b;
  }
  dst[n/2]=0;
  return TSUtf8(dst,n/2);
}

Bool TSParse(U8 *line,I64 length,CTSFrame *frame)
{
  I64 i,crc=0,tab=-1,n=0,d;
  U8 *part[TS_FIELDS_MAX+3];
  if (length<12 || length>=TS_LINE_MAX)
    return FALSE;
  for (i=0;i<length;i++) {
    if (line[i]!='\t' && (line[i]<32 || line[i]>126))
      return FALSE;
    if (line[i]=='\t')
      tab=i;
  }
  if (tab<0 || length-tab!=5)
    return FALSE;
  for (i=tab+1;i<length;i++) {
    d=TSHexDigit(line[i]);
    if (d<0)
      return FALSE;
    crc=(crc<<4)|d;
  }
  if (crc!=TSCRC(line,tab))
    return FALSE;
  line[tab]=0;
  part[n++]=line;
  for (i=0;i<tab;i++) {
    if (line[i]=='\t') {
      if (n>=TS_FIELDS_MAX+3)
        return FALSE;
      line[i]=0;
      part[n++]=line+i+1;
    }
  }
  if (n<3 || StrCmp(part[0],"TS1") || !TSDecimal(part[1],&frame->id)
      || !TSOpcode(part[2]))
    return FALSE;
  TSCopy(frame->op,part[2],sizeof(CTSFrame.op));
  frame->count=n-3;
  for (i=3;i<n;i++)
    if (!TSDecode(part[i],frame->field[i-3]))
      return FALSE;
  return TRUE;
}

I64 TSBuild(U8 *out,I64 id,U8 *op,I64 count,U8 **fields)
{
  I64 i,j,n,l,value;
  U8 *digits="0123456789ABCDEF";
  U8 suffix[16];
  if (id<1 || id>2147483647 || !TSOpcode(op)
      || count<0 || count>TS_FIELDS_MAX)
    return -1;
  StrPrint(out,"TS1\t%d\t%s",id,op);
  n=StrLen(out);
  for (i=0;i<count;i++) {
    l=StrLen(fields[i]);
    if (l>TS_FIELD_MAX || !TSUtf8(fields[i],l))
      return -1;
    if (n+1+MaxI64(1,l*2)+6>TS_LINE_MAX)
      return -1;
    out[n++]='\t';
    if (!l)
      out[n++]='-';
    for (j=0;j<l;j++) {
      value=fields[i][j];
      out[n++]=digits[value>>4];
      out[n++]=digits[value&15];
    }
  }
  StrPrint(suffix,"\t%04X\n",TSCRC(out,n));
  for (i=0;suffix[i];i++)
    out[n++]=suffix[i];
  out[n]=0;
  return n;
}

Bool TSReadReply(I64 expected_id,F64 timeout=60.0)
{
  I64 ch,budget;
  Bool valid;
  F64 deadline=tS+timeout;
  while (tS<deadline) {
    budget=1024;
    while (budget--) {
      ch=TSUartRead;
      if (ch==-1)
        break;
      if (ch==-2) {
        ts_rx_drop=TRUE;
        goto ts_read_next;
      }
      if (ch=='\n') {
        valid=FALSE;
        if (!ts_rx_drop) {
          ts_rx[ts_rx_count]=0;
          valid=TSParse(ts_rx,ts_rx_count,&ts_reply);
        }
        ts_rx_count=0;
        ts_rx_drop=FALSE;
        if (valid && (ts_reply.id==expected_id || ts_reply.id==0))
          return TRUE;
        ts_bad_frames++;
      } else if (!ts_rx_drop) {
        if (ts_rx_count>=TS_LINE_MAX-1)
          ts_rx_drop=TRUE;
        else
          ts_rx[ts_rx_count++]=ch;
      }
ts_read_next:
      ;
    }
    Sleep(1);
  }
  // A partial line is discarded through its eventual newline.
  if (ts_rx_count)
    ts_rx_drop=TRUE;
  return FALSE;
}

Bool TSCall(U8 *op,I64 count=0,U8 *f0=NULL,U8 *f1=NULL,U8 *f2=NULL,
    F64 timeout=60.0)
{
  U8 *fields[3];
  I64 n;
  if (count<0 || count>3)
    return FALSE;
  fields[0]=f0; fields[1]=f1; fields[2]=f2;
  if (++ts_request_id>2147483647)
    ts_request_id=1;
  n=TSBuild(ts_tx,ts_request_id,op,count,fields);
  if (n<0 || !TSUartWrite(ts_tx,n))
    return FALSE;
  return TSReadReply(ts_request_id,timeout);
}
