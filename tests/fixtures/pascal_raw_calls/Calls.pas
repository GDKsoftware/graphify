unit Calls;
interface
type
  TFoo = class
    procedure Q(A: Integer);
  end;
implementation
uses Vcl.Dialogs, Helpers;
procedure TFoo.Q(A: Integer);
var
  Lokaal: Integer;
  procedure Inner(X: Integer);
  begin
    Helpers.InitCap('a');
    Lokaal(1);
  end;
begin
  Inner(1);
  Vcl.Dialogs.ShowMessage('x');
  Self.Q(f(1, 2), 3);
  inherited Q(A);
  inherited;
  Items[0].Refresh;
  Bereken(A, (B + C), [1, 2]);
  Leeg;
end;
end.
