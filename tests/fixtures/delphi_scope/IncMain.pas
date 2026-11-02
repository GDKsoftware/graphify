unit IncMain;

interface

procedure A;
procedure B;
procedure C;

implementation

procedure A;
begin
end;

{$I inc_code.inc}

procedure C;
begin
  B;
end;

end.
