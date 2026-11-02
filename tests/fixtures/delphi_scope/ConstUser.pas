unit ConstUser;

interface

uses ConstLib;

const
  Foo = 1;

var
  Bar: Integer;

procedure TestConst;
procedure TestVar;
procedure TestImplConst;
procedure TestStatement;
procedure TestLocal;

implementation

const
  Baz = 2;

procedure TestConst;
var
  N: Integer;
begin
  N := Foo;
end;

procedure TestVar;
var
  N: Integer;
begin
  N := Bar;
end;

procedure TestImplConst;
var
  N: Integer;
begin
  N := Baz;
end;

procedure TestStatement;
begin
  Foo;
end;

procedure TestLocal;
const
  Zap = 3;
var
  N: Integer;
begin
  N := Zap;
end;

end.
