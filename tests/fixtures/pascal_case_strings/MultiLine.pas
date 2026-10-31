unit MultiLine;

interface

procedure Doe2;

implementation

procedure Volgende;
begin
end;

procedure Tweede;
begin
end;

procedure Verander(Y: Integer);
begin
end;

procedure Doe2;
var
  S, T: string;
begin
  S := '''
    it's initcap(x) // geen commentaar
    end;
    ''';
  Volgende;
  T := '''''
    bevat ''' en Verander(y)
    ''''';
  Tweede;
end;

end.
