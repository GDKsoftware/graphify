unit ParityRtl;

interface

type
  TFoo = class
  public
    constructor Create;
    procedure Doe(S: string);
  end;

implementation

uses SysUtils;

constructor TFoo.Create;
begin
end;

procedure TFoo.Doe(S: string);
var
  F: TFoo;
  N: Integer;
begin
  S := IntToStr(1);
  S := Format('%d', [N]);
  F := TFoo.Create;
  F.Free;
  if Assigned(F) then
    Exit;
  N := Length(S);
  Inc(N);
  SetLength(S, 2);
  WriteLn(S);
  Break;
  Continue;
end;

end.
