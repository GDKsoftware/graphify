unit Nested;
interface
implementation
procedure Fast; assembler;
asm
  mov eax, 1
end;
procedure Next;
begin
  Fast;
end;
procedure Ext; external 'x.dll';
procedure After;
begin
  Next;
end;
type
  TBar = class
    procedure M;
  end;
procedure TBar.M;
  procedure Inner;
  begin
    inherited;
  end;
begin
  Inner;
end;
procedure Last;
begin
end;
end.
