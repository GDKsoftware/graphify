unit ShadowLib;

interface

function TFoo(A: TObject): TObject;
procedure FOnChange(Sender: TObject);
procedure Close;

implementation

function TFoo(A: TObject): TObject;
begin
  Result := A;
end;

procedure FOnChange(Sender: TObject);
begin
end;

procedure Close;
begin
end;

end.
