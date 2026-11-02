unit ConstUsed;

interface

uses ConstLib, ConstShadowLib;

procedure TestGlobal;
procedure TestImplConst;
procedure TestEnum;

implementation

procedure TestGlobal;
var
  N: Integer;
begin
  N := Zap;
end;

procedure TestImplConst;
var
  N: Integer;
begin
  N := Quux;
end;

procedure TestEnum;
var
  N: TKleur;
begin
  N := kRood;
end;

end.
